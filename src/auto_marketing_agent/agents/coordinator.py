"""Campaign Coordinator —— Python 层的 agent 编排。

P0 只串联 Orchestrator → Audience → Creative 线性三步。之所以用 Python 代码串(而不是
SDK handoff):

- 可以在 CI 里对每一步注入假 Agent 做确定性断言(见 tests/agents/test_coordinator.py)。
- handoff 更适合对话里的专家化路由;P0 的诉求是"拿到一份 brief 跑一遍,落三份结构化输出",
  线性函数更直接。
- 真正的事件驱动 handoff(Attribution ROAS 下滑 → Creative 回炉)在 P2 接入,届时会把这个
  coordinator 包成一条 replay-able 的 event 流。

每次跑 coordinator,都会用 `campaign_trace` 挂一个 SDK trace,`group_id = campaign_id`,
满足架构 §3.7 "每个 Campaign 一个 Session" 的初步对齐。
"""

from __future__ import annotations

from dataclasses import dataclass

from agents import Agent, Runner
from auto_marketing_agent.agents.audience import build_audience_agent
from auto_marketing_agent.agents.creative import build_creative_agent
from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent
from auto_marketing_agent.schemas.v1.audience import AudienceSegment
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan
from auto_marketing_agent.schemas.v1.creative import CreativeVariant
from auto_marketing_agent.tracing import campaign_trace


@dataclass(frozen=True, slots=True)
class CampaignRunResult:
    """一次 campaign 编排的完整结果。三份 payload 互相引用:

    - plan.campaign_id == segment.campaign_id == variant.campaign_id
    - variant.target_segment_id == segment.segment_id
    - 三者共享同一个 correlation_id

    这些一致性由 coordinator 负责保证,agent 的 instructions 只提要求,
    coordinator 在组装完成后会再做一次断言(`_check_consistency`)。
    """

    plan: CampaignPlan
    segment: AudienceSegment
    variant: CreativeVariant


@dataclass(frozen=True, slots=True)
class CampaignAgents:
    """注入点 —— 允许测试替换为 stub Agent,避免真实调用模型。"""

    orchestrator: Agent[None]
    audience: Agent[None]
    creative: Agent[None]


def build_default_agents(model: str) -> CampaignAgents:
    return CampaignAgents(
        orchestrator=build_orchestrator_agent(model=model),
        audience=build_audience_agent(model=model),
        creative=build_creative_agent(model=model),
    )


def _check_consistency(result: CampaignRunResult) -> None:
    campaign_ids = {result.plan.campaign_id, result.segment.campaign_id, result.variant.campaign_id}
    if len(campaign_ids) != 1:
        raise ValueError(f"campaign_id 在 plan/segment/variant 间不一致: {campaign_ids}")
    if result.variant.target_segment_id != result.segment.segment_id:
        raise ValueError(
            "variant.target_segment_id 未指向 segment.segment_id:"
            f"{result.variant.target_segment_id} vs {result.segment.segment_id}"
        )
    correlation_ids = {
        result.plan.correlation_id,
        result.segment.correlation_id,
        result.variant.correlation_id,
    }
    if len(correlation_ids) != 1:
        raise ValueError(f"correlation_id 在三份 payload 间不一致: {correlation_ids}")


async def run_campaign(
    brief: str,
    *,
    correlation_id: str,
    agents: CampaignAgents,
) -> CampaignRunResult:
    """按 brief 跑完 Orchestrator → Audience → Creative 三步。

    `correlation_id` 由上游(CLI / event bus)传入,sender 负责去重。`campaign_id` 由
    Orchestrator agent 自己决定并写进 CampaignPlan,coordinator 不干预。
    """
    orch_input = f"correlation_id={correlation_id}\n\nbrief:\n{brief}"
    plan_run = await Runner.run(agents.orchestrator, orch_input)
    plan = plan_run.final_output_as(CampaignPlan)

    with campaign_trace(campaign_id=plan.campaign_id, phase="plan"):
        audience_input = (
            f"correlation_id={correlation_id}\n"
            f"campaign_id={plan.campaign_id}\n\n"
            f"CampaignPlan JSON:\n{plan.model_dump_json()}"
        )
        segment_run = await Runner.run(agents.audience, audience_input)
        segment = segment_run.final_output_as(AudienceSegment)

        creative_input = (
            f"correlation_id={correlation_id}\n"
            f"campaign_id={plan.campaign_id}\n\n"
            f"CampaignPlan JSON:\n{plan.model_dump_json()}\n\n"
            f"AudienceSegment JSON:\n{segment.model_dump_json()}"
        )
        variant_run = await Runner.run(agents.creative, creative_input)
        variant = variant_run.final_output_as(CreativeVariant)

    result = CampaignRunResult(plan=plan, segment=segment, variant=variant)
    _check_consistency(result)
    return result
