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
from typing import Any

from agents import Agent, Runner
from auto_marketing_agent.agents.audience import build_audience_agent
from auto_marketing_agent.agents.creative import build_creative_agent
from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent
from auto_marketing_agent.cost_guard import (
    CostGuard,
    CostGuardDecision,
    estimate_prompt_tokens,
)
from auto_marketing_agent.guardrail import GuardrailEngine, default_engine
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.audience import AudienceSegment
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan
from auto_marketing_agent.schemas.v1.creative import CreativeVariant
from auto_marketing_agent.tracing import campaign_trace


class CreativeRejected(RuntimeError):
    """Guardrail 机审 reject 时抛出。

    和 `CostGuardDenied` 的定位一致:这是业务拒绝,不可重试 —— 上游必须让 Creative
    用 `modification_suggestions` 重新生成,或走 HITL 流人工覆盖。
    """

    def __init__(self, decision: ApprovalDecision) -> None:
        super().__init__(
            f"CreativeVariant {decision.subject_id} 机审 reject: violations={decision.violations}"
        )
        self.decision = decision


# 不同 agent 产出长度差异大,给 output_tokens 估算留一个档位表。L2 接入真实 tokenizer
# 后会按 model + schema 反推;P1-001 先用静态预估即可。
_ESTIMATED_OUTPUT_TOKENS: dict[str, int] = {
    "orchestrator-agent": 400,
    "audience-agent": 400,
    "creative-agent": 800,
}
_DEFAULT_OUTPUT_TOKENS = 400


@dataclass(frozen=True, slots=True)
class CampaignRunResult:
    """一次 campaign 编排的完整结果。三份 payload 互相引用:

    - plan.campaign_id == segment.campaign_id == variant.campaign_id
    - variant.target_segment_id == segment.segment_id
    - 三者共享同一个 correlation_id

    这些一致性由 coordinator 负责保证,agent 的 instructions 只提要求,
    coordinator 在组装完成后会再做一次断言(`_check_consistency`)。

    `approval` 是 Guardrail 机审结果。approve / needs_hitl 都会返回结果,
    reject 直接抛 `CreativeRejected`,不会走到这里。
    """

    plan: CampaignPlan
    segment: AudienceSegment
    variant: CreativeVariant
    approval: ApprovalDecision


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


async def _authorized_run(
    *,
    agent: Agent[None],
    prompt: str,
    cost_guard: CostGuard,
    decisions: list[CostGuardDecision],
) -> Any:
    """调 Cost Guard L1 放行后再跑 Runner。

    拒绝时 `CostGuardDenied` 直接向上抛,由 caller(run_campaign / 未来的 HITL 流)决定
    是否重试。放行 decision 追加进 `decisions`,供 P1-021 Event Store 整批 flush。
    """
    output_budget = _ESTIMATED_OUTPUT_TOKENS.get(agent.name, _DEFAULT_OUTPUT_TOKENS)
    decision = cost_guard.authorize_call(
        agent_name=agent.name,
        estimated_prompt_tokens=estimate_prompt_tokens(prompt),
        estimated_output_tokens=output_budget,
    )
    decisions.append(decision)
    return await Runner.run(agent, prompt)


async def run_campaign(
    brief: str,
    *,
    correlation_id: str,
    agents: CampaignAgents,
    cost_guard: CostGuard | None = None,
    guardrail_engine: GuardrailEngine | None = None,
) -> CampaignRunResult:
    """按 brief 跑完 Orchestrator → Audience → Creative → Guardrail 四步。

    `correlation_id` 由上游(CLI / event bus)传入,sender 负责去重。`campaign_id` 由
    Orchestrator agent 自己决定并写进 CampaignPlan,coordinator 不干预。

    每次 Runner.run 前过 Cost Guard L1(CLAUDE.md 硬约束:每次 LLM 调用前必须过)。
    `cost_guard=None` 时用默认保守配置;传入自定义 guard 可以做更严苛的上限。

    Creative 产出后立即过 Guardrail 机审 —— 确定性规则,不调 LLM,零成本。reject 直接
    抛 `CreativeRejected`,不让违规素材进入下游(未来的 Media Buyer / Event Store)。
    needs_hitl 也放行,由 caller(P1-053 HITL 队列)决定是否阻塞投放。
    """
    guard = cost_guard or CostGuard()
    engine = guardrail_engine or default_engine()
    decisions: list[CostGuardDecision] = []

    orch_input = f"correlation_id={correlation_id}\n\nbrief:\n{brief}"
    plan_run = await _authorized_run(
        agent=agents.orchestrator,
        prompt=orch_input,
        cost_guard=guard,
        decisions=decisions,
    )
    plan = plan_run.final_output_as(CampaignPlan)

    with campaign_trace(campaign_id=plan.campaign_id, phase="plan"):
        audience_input = (
            f"correlation_id={correlation_id}\n"
            f"campaign_id={plan.campaign_id}\n\n"
            f"CampaignPlan JSON:\n{plan.model_dump_json()}"
        )
        segment_run = await _authorized_run(
            agent=agents.audience,
            prompt=audience_input,
            cost_guard=guard,
            decisions=decisions,
        )
        segment = segment_run.final_output_as(AudienceSegment)

        creative_input = (
            f"correlation_id={correlation_id}\n"
            f"campaign_id={plan.campaign_id}\n\n"
            f"CampaignPlan JSON:\n{plan.model_dump_json()}\n\n"
            f"AudienceSegment JSON:\n{segment.model_dump_json()}"
        )
        variant_run = await _authorized_run(
            agent=agents.creative,
            prompt=creative_input,
            cost_guard=guard,
            decisions=decisions,
        )
        variant = variant_run.final_output_as(CreativeVariant)

    approval = engine.evaluate_variant(
        variant,
        brand_guardrails=list(plan.brand_guardrails),
        correlation_id=correlation_id,
    )
    if approval.decision == "reject":
        raise CreativeRejected(approval)

    result = CampaignRunResult(plan=plan, segment=segment, variant=variant, approval=approval)
    _check_consistency(result)
    return result
