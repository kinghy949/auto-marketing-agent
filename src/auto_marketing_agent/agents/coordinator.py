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
    CostGuardDenied,
    estimate_prompt_tokens,
)
from auto_marketing_agent.events import Event, EventStore, EventType
from auto_marketing_agent.guardrail import GuardrailEngine, default_engine
from auto_marketing_agent.hitl import HitlItem, HitlQueue
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

    `hitl_item` 仅在 `approval.decision == "needs_hitl"` 且 caller 传入了
    `hitl_queue` 时才非空 —— coordinator 在返回前已把工单入队,caller 拿到
    `HitlItem` 就可以建索引 / 转交 UI,不用自己去查队列。
    """

    plan: CampaignPlan
    segment: AudienceSegment
    variant: CreativeVariant
    approval: ApprovalDecision
    hitl_item: HitlItem | None = None


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


def _emit_event(
    event_store: EventStore | None,
    *,
    event_type: EventType,
    source: str,
    correlation_id: str,
    campaign_id: str | None,
    payload: dict[str, Any],
) -> None:
    """如传入了 event_store,就 append 一条事件;否则静默跳过。

    不 try/except 包裹 —— append 失败是基础设施故障,应该向上冒泡而不是吞掉。
    coordinator 本身不做 event 写入重试(那是 P2 Postgres 后端自己的职责)。
    """
    if event_store is None:
        return
    event_store.append(
        Event(
            event_type=event_type,
            source=source,
            correlation_id=correlation_id,
            campaign_id=campaign_id,
            payload=payload,
        )
    )


async def _authorized_run(
    *,
    agent: Agent[None],
    prompt: str,
    cost_guard: CostGuard,
    decisions: list[CostGuardDecision],
    event_store: EventStore | None,
    correlation_id: str,
    campaign_id: str | None,
) -> Any:
    """调 Cost Guard L1 放行后再跑 Runner。

    Cost Guard 放行发 `cost_guard.authorized`;拒绝发 `cost_guard.denied` 再抛
    `CostGuardDenied`,由 caller 决定是否重试。`decisions` 列表保留原职责,供
    P2 批量 flush 使用;`event_store` 是即时写入的独立通道,互不替代。
    """
    output_budget = _ESTIMATED_OUTPUT_TOKENS.get(agent.name, _DEFAULT_OUTPUT_TOKENS)
    try:
        decision = cost_guard.authorize_call(
            agent_name=agent.name,
            estimated_prompt_tokens=estimate_prompt_tokens(prompt),
            estimated_output_tokens=output_budget,
        )
    except CostGuardDenied as denial:
        _emit_event(
            event_store,
            event_type="cost_guard.denied",
            source="coordinator",
            correlation_id=correlation_id,
            campaign_id=campaign_id,
            payload={
                "agent_name": denial.agent_name,
                "level": denial.level,
                "limit_kind": denial.limit_kind,
                "observed": denial.observed,
                "limit": denial.limit,
            },
        )
        raise
    decisions.append(decision)
    _emit_event(
        event_store,
        event_type="cost_guard.authorized",
        source="coordinator",
        correlation_id=correlation_id,
        campaign_id=campaign_id,
        payload={
            "agent_name": agent.name,
            "level": decision.level,
            "estimated_prompt_tokens": decision.estimated_prompt_tokens,
            "estimated_output_tokens": decision.estimated_output_tokens,
        },
    )
    return await Runner.run(agent, prompt)


def _format_replan_hint(denial: CostGuardDenied) -> str:
    """把 Cost Guard 拒绝原因渲染成给 Orchestrator 看的中文提示。

    Orchestrator 拿到这段文本会追加到下一轮 brief 末尾,从而产出更精简的 CampaignPlan
    (更短的 brand_guardrails / description / platforms)。措辞刻意具体化 limit_kind 与
    observed/limit,方便模型定位压缩方向。
    """
    return (
        f"[Cost Guard 重规划提示] 上一轮规划在 {denial.agent_name} 阶段被 "
        f"Cost Guard {denial.level} 拒绝(limit_kind={denial.limit_kind},"
        f"observed={denial.observed},limit={denial.limit})。"
        "请产出更精简的 CampaignPlan:优先压缩 brand_guardrails 条目数量与措辞长度,"
        "必要时收敛 platforms 列表与 description 文本,务必保持核心 KPI 与预算不变。"
    )


async def _run_campaign_once(
    brief: str,
    *,
    correlation_id: str,
    agents: CampaignAgents,
    cost_guard: CostGuard,
    guardrail_engine: GuardrailEngine,
    hitl_queue: HitlQueue | None,
    event_store: EventStore | None,
) -> CampaignRunResult:
    """单次 Orchestrator → Audience → Creative → Guardrail 闭环。

    成功返回结果;任何 Cost Guard 拒绝都原样抛出,由外层 `run_campaign` 决定是否重规划。
    Guardrail `reject` 抛 `CreativeRejected`,不在重规划覆盖范围 —— 内容违规重跑规划
    也解决不了。

    事件发射时序:
    - Orchestrator 之前还没有 campaign_id,cost_guard 事件只带 correlation_id。
    - Audience / Creative 之前已经有 plan.campaign_id,cost_guard 事件带 campaign_id。
    - Guardrail 评估结果发 `guardrail.evaluated`;reject 再补一条 `creative.rejected`
      再抛异常;needs_hitl 入队后发 `hitl.enqueued`。
    - 成功回到 caller 前发 `campaign.completed`。
    """
    decisions: list[CostGuardDecision] = []

    orch_input = f"correlation_id={correlation_id}\n\nbrief:\n{brief}"
    plan_run = await _authorized_run(
        agent=agents.orchestrator,
        prompt=orch_input,
        cost_guard=cost_guard,
        decisions=decisions,
        event_store=event_store,
        correlation_id=correlation_id,
        campaign_id=None,
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
            cost_guard=cost_guard,
            decisions=decisions,
            event_store=event_store,
            correlation_id=correlation_id,
            campaign_id=plan.campaign_id,
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
            cost_guard=cost_guard,
            decisions=decisions,
            event_store=event_store,
            correlation_id=correlation_id,
            campaign_id=plan.campaign_id,
        )
        variant = variant_run.final_output_as(CreativeVariant)

    approval = guardrail_engine.evaluate_variant(
        variant,
        brand_guardrails=list(plan.brand_guardrails),
        correlation_id=correlation_id,
    )
    _emit_event(
        event_store,
        event_type="guardrail.evaluated",
        source="guardrail",
        correlation_id=correlation_id,
        campaign_id=plan.campaign_id,
        payload={
            "subject_id": approval.subject_id,
            "decision": approval.decision,
            "violations": list(approval.violations),
        },
    )
    if approval.decision == "reject":
        _emit_event(
            event_store,
            event_type="creative.rejected",
            source="coordinator",
            correlation_id=correlation_id,
            campaign_id=plan.campaign_id,
            payload={
                "subject_id": approval.subject_id,
                "violations": list(approval.violations),
            },
        )
        raise CreativeRejected(approval)

    hitl_item: HitlItem | None = None
    if approval.decision == "needs_hitl" and hitl_queue is not None:
        hitl_item = hitl_queue.enqueue(
            approval=approval,
            variant=variant,
            brand_guardrails=tuple(plan.brand_guardrails),
        )
        _emit_event(
            event_store,
            event_type="hitl.enqueued",
            source="hitl",
            correlation_id=correlation_id,
            campaign_id=plan.campaign_id,
            payload={
                "hitl_item_id": hitl_item.item_id,
                "approval_id": approval.approval_id,
            },
        )

    result = CampaignRunResult(
        plan=plan,
        segment=segment,
        variant=variant,
        approval=approval,
        hitl_item=hitl_item,
    )
    _check_consistency(result)
    _emit_event(
        event_store,
        event_type="campaign.completed",
        source="coordinator",
        correlation_id=correlation_id,
        campaign_id=plan.campaign_id,
        payload={
            "campaign_id": plan.campaign_id,
            "segment_id": segment.segment_id,
            "variant_id": variant.variant_id,
            "approval_decision": approval.decision,
        },
    )
    return result


async def run_campaign(
    brief: str,
    *,
    correlation_id: str,
    agents: CampaignAgents,
    cost_guard: CostGuard | None = None,
    guardrail_engine: GuardrailEngine | None = None,
    hitl_queue: HitlQueue | None = None,
    event_store: EventStore | None = None,
    max_cost_replans: int = 1,
) -> CampaignRunResult:
    """按 brief 跑完 Orchestrator → Audience → Creative → Guardrail 四步。

    `correlation_id` 由上游(CLI / event bus)传入,sender 负责去重。`campaign_id` 由
    Orchestrator agent 自己决定并写进 CampaignPlan,coordinator 不干预。

    每次 Runner.run 前过 Cost Guard L1(CLAUDE.md 硬约束:每次 LLM 调用前必须过)。
    `cost_guard=None` 时用默认保守配置;传入自定义 guard 可以做更严苛的上限。

    Creative 产出后立即过 Guardrail 机审 —— 确定性规则,不调 LLM,零成本。reject 直接
    抛 `CreativeRejected`,不让违规素材进入下游(未来的 Media Buyer / Event Store)。
    needs_hitl 会入 `hitl_queue`(如传入),coordinator 本身不阻塞 —— 是否暂停投放
    由 caller(Web UI / 事件消费者)依据工单状态决定。

    **重规划(P1-004):** Audience / Creative 阶段被 Cost Guard 拒时,coordinator
    会把拒绝上下文追加到 brief,重跑 Orchestrator → Audience → Creative 链,让
    Orchestrator 产出更精简的 CampaignPlan。最多重试 `max_cost_replans` 次,超限或
    Orchestrator 自身被拒(brief 太长没法自动压缩)直接把最后一次的 `CostGuardDenied`
    抛出。`max_cost_replans=0` 完全关闭重规划,任何拒绝直接抛。每次触发重规划发
    `cost_replan.triggered`,方便重放时还原 brief 的演化轨迹。

    **事件(P1-020/021):** 如传入 `event_store`,coordinator 会在 cost_guard /
    guardrail / creative.rejected / hitl.enqueued / cost_replan.triggered /
    campaign.completed 六个位点 append 事件。不传则全链路静默运行,单测 CLI 默认
    不开,生产 CLI 与 P2 worker 必须开。
    """
    if max_cost_replans < 0:
        raise ValueError(f"max_cost_replans 必须 >= 0,收到 {max_cost_replans}")

    guard = cost_guard or CostGuard()
    engine = guardrail_engine or default_engine()

    attempts_remaining = max_cost_replans + 1
    current_brief = brief
    attempt_number = 0
    while True:
        attempts_remaining -= 1
        attempt_number += 1
        try:
            return await _run_campaign_once(
                brief=current_brief,
                correlation_id=correlation_id,
                agents=agents,
                cost_guard=guard,
                guardrail_engine=engine,
                hitl_queue=hitl_queue,
                event_store=event_store,
            )
        except CostGuardDenied as denial:
            # Orchestrator 自身被拒 = brief 太长,没法再压缩;重试只会卡在同一步。
            if denial.agent_name == "orchestrator-agent":
                raise
            if attempts_remaining <= 0:
                raise
            _emit_event(
                event_store,
                event_type="cost_replan.triggered",
                source="coordinator",
                correlation_id=correlation_id,
                campaign_id=None,
                payload={
                    "denied_agent": denial.agent_name,
                    "limit_kind": denial.limit_kind,
                    "attempt_number": attempt_number,
                },
            )
            current_brief = f"{brief}\n\n{_format_replan_hint(denial)}"
