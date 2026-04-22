"""Coordinator 测试 —— 用 stub Agent 验证三段编排与一致性校验。

不调真实模型:每个 Agent 的 `Runner.run` 被 monkeypatch 替换为产出预置 payload 的
协程,这让 coordinator 行为完全可测,且不占 CI 额度。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from auto_marketing_agent.agents import coordinator as coordinator_mod
from auto_marketing_agent.agents.coordinator import (
    CampaignAgents,
    CreativeRejected,
    _check_consistency,
    run_campaign,
)
from auto_marketing_agent.cost_guard import CostGuard, CostGuardConfig, CostGuardDenied
from auto_marketing_agent.events import InMemoryEventStore
from auto_marketing_agent.hitl import InMemoryHitlQueue
from auto_marketing_agent.schemas.common import KPITarget, Money
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.audience import AudienceSegment
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan
from auto_marketing_agent.schemas.v1.creative import (
    AssetRights,
    CreativeAsset,
    CreativeVariant,
)

CORRELATION = "corr-test-1"
CAMPAIGN_ID = "cmp:unit:202604"


def _plan() -> CampaignPlan:
    return CampaignPlan(
        correlation_id=CORRELATION,
        campaign_id=CAMPAIGN_ID,
        name="单元测试活动",
        primary_kpi=KPITarget(metric="roas", target=3.0, comparison="gte"),
        daily_budget=Money(amount=Decimal("1000"), currency="USD"),
        total_budget=Money(amount=Decimal("30000"), currency="USD"),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 30),
        platforms=["meta"],
        brand_guardrails=["禁用赌博"],
    )


def _segment() -> AudienceSegment:
    return AudienceSegment(
        correlation_id=CORRELATION,
        segment_id="aud:unit:main",
        campaign_id=CAMPAIGN_ID,
        name="主受众",
        description="测试用",
        size_estimate=100_000,
        hashed_user_ids=["h:sha256:a", "h:sha256:b"],
        attributes={"geo": "US"},
        source="cdp:segment:cdp:us_young_sporty",
    )


def _variant(
    target_segment_id: str = "aud:unit:main",
    *,
    headline: str = "测试标题",
    body: str = "测试正文",
) -> CreativeVariant:
    return CreativeVariant(
        correlation_id=CORRELATION,
        variant_id="var:unit:main",
        campaign_id=CAMPAIGN_ID,
        target_segment_id=target_segment_id,
        headline=headline,
        body=body,
        call_to_action="立即购买",
        language="en-US",
        assets=[
            CreativeAsset(
                asset_id="asset:x",
                asset_type="text",
                text="slogan",
                rights=AssetRights(licensor="internal", license_id="lic-1"),
            )
        ],
        generated_by="creative-agent/gpt-4.1-mini",
    )


def _approval(decision: str = "approve") -> ApprovalDecision:
    return ApprovalDecision(
        correlation_id=CORRELATION,
        approval_id="apv:var:unit:main:deadbeef",
        campaign_id=CAMPAIGN_ID,
        subject_type="creative_variant",
        subject_id="var:unit:main",
        decision=decision,
        rationale="unit stub",
        violations=["stub:rule"] if decision == "reject" else [],
    )


@dataclass
class _StubRunResult:
    payload: Any

    def final_output_as(self, _cls: Any) -> Any:
        return self.payload


@pytest.fixture
def stub_runner(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """每次调 Runner.run 按预置顺序返回:plan → segment → variant。"""
    queue: list[Any] = [_plan(), _segment(), _variant()]
    calls: list[Any] = []

    async def fake_run(agent: Any, input_: Any, **kwargs: Any) -> _StubRunResult:
        calls.append((agent.name, input_))
        return _StubRunResult(queue.pop(0))

    # 走字符串路径:coordinator_mod.Runner 是从 agents 包 re-import 的,mypy strict 下
    # attr-defined 不认可,改用属性路径一次性打桩。
    monkeypatch.setattr("auto_marketing_agent.agents.coordinator.Runner.run", fake_run)
    return calls


@pytest.mark.asyncio
async def test_run_campaign_chains_three_agents_in_order(stub_runner: list[Any]) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )

    result = await run_campaign(
        brief="跑一次美国年轻运动人群的 roas 活动",
        correlation_id=CORRELATION,
        agents=agents,
    )

    assert [name for name, _ in stub_runner] == [
        "orchestrator-agent",
        "audience-agent",
        "creative-agent",
    ]
    assert result.plan.campaign_id == CAMPAIGN_ID
    assert result.segment.campaign_id == CAMPAIGN_ID
    assert result.variant.target_segment_id == result.segment.segment_id
    # 默认 stub variant 文本干净,机审应 approve
    assert result.approval.decision == "approve"
    assert result.approval.subject_id == result.variant.variant_id


@pytest.mark.asyncio
async def test_run_campaign_passes_plan_and_segment_to_downstream(
    stub_runner: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )

    await run_campaign(brief="x", correlation_id=CORRELATION, agents=agents)

    # Audience 拿到的 input 必须带 CampaignPlan JSON
    _, audience_input = stub_runner[1]
    assert "CampaignPlan JSON" in audience_input
    assert CAMPAIGN_ID in audience_input

    # Creative 拿到的 input 必须同时带 CampaignPlan 与 AudienceSegment JSON
    _, creative_input = stub_runner[2]
    assert "CampaignPlan JSON" in creative_input
    assert "AudienceSegment JSON" in creative_input


def test_consistency_check_catches_campaign_id_mismatch() -> None:
    bad = coordinator_mod.CampaignRunResult(
        plan=_plan(),
        segment=_segment().model_copy(update={"campaign_id": "other-id"}),
        variant=_variant(),
        approval=_approval(),
    )
    with pytest.raises(ValueError, match="campaign_id"):
        _check_consistency(bad)


def test_consistency_check_catches_target_segment_mismatch() -> None:
    bad = coordinator_mod.CampaignRunResult(
        plan=_plan(),
        segment=_segment(),
        variant=_variant(target_segment_id="aud:wrong"),
        approval=_approval(),
    )
    with pytest.raises(ValueError, match="target_segment_id"):
        _check_consistency(bad)


def test_consistency_check_catches_correlation_id_mismatch() -> None:
    bad = coordinator_mod.CampaignRunResult(
        plan=_plan(),
        segment=_segment().model_copy(update={"correlation_id": "other-corr"}),
        variant=_variant(),
        approval=_approval(),
    )
    with pytest.raises(ValueError, match="correlation_id"):
        _check_consistency(bad)


@pytest.fixture
def stub_runner_with_reject_variant(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Creative 输出含绝对化用语,触发 Guardrail reject。"""
    queue: list[Any] = [
        _plan(),
        _segment(),
        _variant(headline="顶级国家级享受"),  # 两处命中 cn_ad_law:absolute_superlatives
    ]
    calls: list[Any] = []

    async def fake_run(agent: Any, input_: Any, **kwargs: Any) -> _StubRunResult:
        calls.append((agent.name, input_))
        return _StubRunResult(queue.pop(0))

    monkeypatch.setattr("auto_marketing_agent.agents.coordinator.Runner.run", fake_run)
    return calls


@pytest.mark.asyncio
async def test_run_campaign_raises_creative_rejected_when_guardrail_rejects(
    stub_runner_with_reject_variant: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )

    with pytest.raises(CreativeRejected) as exc:
        await run_campaign(brief="x", correlation_id=CORRELATION, agents=agents)

    assert exc.value.decision.decision == "reject"
    assert "cn_ad_law:absolute_superlatives" in exc.value.decision.violations


@pytest.fixture
def stub_runner_with_hitl_variant(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Creative 输出含疑似医疗宣称,触发 Guardrail needs_hitl。"""
    queue: list[Any] = [
        _plan(),
        _segment(),
        _variant(body="使用三天即可根治,无副作用"),  # cn_ad_law:unapproved_medical_claims
    ]
    calls: list[Any] = []

    async def fake_run(agent: Any, input_: Any, **kwargs: Any) -> _StubRunResult:
        calls.append((agent.name, input_))
        return _StubRunResult(queue.pop(0))

    monkeypatch.setattr("auto_marketing_agent.agents.coordinator.Runner.run", fake_run)
    return calls


@pytest.mark.asyncio
async def test_run_campaign_enqueues_hitl_when_decision_is_needs_hitl(
    stub_runner_with_hitl_variant: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )
    queue = InMemoryHitlQueue()

    result = await run_campaign(
        brief="x",
        correlation_id=CORRELATION,
        agents=agents,
        hitl_queue=queue,
    )

    assert result.approval.decision == "needs_hitl"
    assert result.hitl_item is not None
    assert result.hitl_item.status == "pending"
    assert result.hitl_item.approval.approval_id == result.approval.approval_id
    assert "禁用赌博" in result.hitl_item.brand_guardrails
    # 队列里也能查到同一条
    pending = queue.list_pending()
    assert len(pending) == 1
    assert pending[0].item_id == result.hitl_item.item_id


@pytest.mark.asyncio
async def test_run_campaign_returns_no_hitl_item_when_approve(
    stub_runner: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )
    queue = InMemoryHitlQueue()

    result = await run_campaign(
        brief="x",
        correlation_id=CORRELATION,
        agents=agents,
        hitl_queue=queue,
    )

    assert result.approval.decision == "approve"
    assert result.hitl_item is None
    assert queue.list_pending() == []


@pytest.mark.asyncio
async def test_run_campaign_without_hitl_queue_still_returns_approval(
    stub_runner_with_hitl_variant: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )

    result = await run_campaign(brief="x", correlation_id=CORRELATION, agents=agents)

    assert result.approval.decision == "needs_hitl"
    assert result.hitl_item is None


@pytest.mark.asyncio
async def test_run_campaign_raises_when_cost_guard_denies(
    stub_runner: list[Any],
) -> None:
    # 用一个比任何 agent 都激进的上限,保证第一次 authorize_call 就拒
    strict_guard = CostGuard(CostGuardConfig(1, 1, 1))

    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )

    with pytest.raises(CostGuardDenied) as exc:
        await run_campaign(
            brief="x",
            correlation_id=CORRELATION,
            agents=agents,
            cost_guard=strict_guard,
        )
    # 第一步 orchestrator 就被拒 —— brief 无法自动压缩,replan 应跳过直接抛
    assert exc.value.agent_name == "orchestrator-agent"
    # 被拒前 Runner.run 不应被调用
    assert stub_runner == []


class _DenyAgentFirstTimesCostGuard(CostGuard):
    """对指定 agent 的前 N 次调用硬拒,之后放行。用来模拟"第 1 次规划超限、重规划后通过"。"""

    def __init__(self, *, deny_agent: str, deny_times: int = 1) -> None:
        super().__init__()
        self._deny_agent = deny_agent
        self._remaining_denials = deny_times

    def authorize_call(
        self,
        *,
        agent_name: str,
        estimated_prompt_tokens: int,
        estimated_output_tokens: int,
    ) -> Any:
        if agent_name == self._deny_agent and self._remaining_denials > 0:
            self._remaining_denials -= 1
            raise CostGuardDenied(
                level="L1",
                limit_kind="prompt_tokens",
                observed=9_999,
                limit=100,
                agent_name=agent_name,
            )
        return super().authorize_call(
            agent_name=agent_name,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )


@pytest.fixture
def stub_runner_for_replan(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Audience 首次被拒 → 重规划成功场景。

    Runner.run 会被调用 4 次:
    - 尝试 1:orchestrator → plan(audience 调用前被 Cost Guard 拦下,Runner 不触发)
    - 尝试 2:orchestrator → plan、audience → segment、creative → variant
    """
    queue: list[Any] = [_plan(), _plan(), _segment(), _variant()]
    calls: list[Any] = []

    async def fake_run(agent: Any, input_: Any, **kwargs: Any) -> _StubRunResult:
        calls.append((agent.name, input_))
        return _StubRunResult(queue.pop(0))

    monkeypatch.setattr("auto_marketing_agent.agents.coordinator.Runner.run", fake_run)
    return calls


@pytest.mark.asyncio
async def test_run_campaign_replans_once_when_audience_denied(
    stub_runner_for_replan: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )
    guard = _DenyAgentFirstTimesCostGuard(deny_agent="audience-agent", deny_times=1)

    result = await run_campaign(
        brief="初始 brief",
        correlation_id=CORRELATION,
        agents=agents,
        cost_guard=guard,
    )

    # 成功返回,说明第二次尝试通过
    assert result.plan.campaign_id == CAMPAIGN_ID
    # Runner 一共被调用 4 次:orch(1), orch(2), audience(2), creative(2)
    assert [name for name, _ in stub_runner_for_replan] == [
        "orchestrator-agent",
        "orchestrator-agent",
        "audience-agent",
        "creative-agent",
    ]
    # 第二次 orchestrator 的 brief 里必须带重规划提示,且包含原始 brief
    second_orch_input = stub_runner_for_replan[1][1]
    assert "初始 brief" in second_orch_input
    assert "Cost Guard 重规划提示" in second_orch_input
    assert "audience-agent" in second_orch_input


@pytest.fixture
def stub_runner_for_exhausted_replan(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Audience 连续两次被拒 —— 重规划上限耗尽后抛出。"""
    queue: list[Any] = [_plan(), _plan()]
    calls: list[Any] = []

    async def fake_run(agent: Any, input_: Any, **kwargs: Any) -> _StubRunResult:
        calls.append((agent.name, input_))
        return _StubRunResult(queue.pop(0))

    monkeypatch.setattr("auto_marketing_agent.agents.coordinator.Runner.run", fake_run)
    return calls


@pytest.mark.asyncio
async def test_run_campaign_reraises_when_replan_budget_exhausted(
    stub_runner_for_exhausted_replan: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )
    guard = _DenyAgentFirstTimesCostGuard(deny_agent="audience-agent", deny_times=2)

    with pytest.raises(CostGuardDenied) as exc:
        await run_campaign(
            brief="x",
            correlation_id=CORRELATION,
            agents=agents,
            cost_guard=guard,
            max_cost_replans=1,
        )
    assert exc.value.agent_name == "audience-agent"
    # 两次 orchestrator 都跑了,第二次 audience 又被拒后不再重试
    assert [name for name, _ in stub_runner_for_exhausted_replan] == [
        "orchestrator-agent",
        "orchestrator-agent",
    ]


@pytest.mark.asyncio
async def test_run_campaign_with_zero_replans_does_not_retry(
    stub_runner: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )
    guard = _DenyAgentFirstTimesCostGuard(deny_agent="audience-agent", deny_times=1)

    with pytest.raises(CostGuardDenied):
        await run_campaign(
            brief="x",
            correlation_id=CORRELATION,
            agents=agents,
            cost_guard=guard,
            max_cost_replans=0,
        )
    # max_cost_replans=0 时只跑了一次 orchestrator,不进重规划
    assert [name for name, _ in stub_runner] == ["orchestrator-agent"]


@pytest.mark.asyncio
async def test_run_campaign_rejects_negative_max_cost_replans(
    stub_runner: list[Any],
) -> None:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    agents = CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )
    with pytest.raises(ValueError, match="max_cost_replans"):
        await run_campaign(
            brief="x",
            correlation_id=CORRELATION,
            agents=agents,
            max_cost_replans=-1,
        )


# ---------------------------------------------------------------------------
# Event Store 集成 —— P1-020/021
# ---------------------------------------------------------------------------


def _build_default_agents() -> CampaignAgents:
    from auto_marketing_agent.agents.audience import build_audience_agent
    from auto_marketing_agent.agents.creative import build_creative_agent
    from auto_marketing_agent.agents.orchestrator import build_orchestrator_agent

    return CampaignAgents(
        orchestrator=build_orchestrator_agent(model="stub"),
        audience=build_audience_agent(model="stub"),
        creative=build_creative_agent(model="stub"),
    )


@pytest.mark.asyncio
async def test_event_store_records_happy_path_emission_order(
    stub_runner: list[Any],
) -> None:
    store = InMemoryEventStore()
    await run_campaign(
        brief="x",
        correlation_id=CORRELATION,
        agents=_build_default_agents(),
        event_store=store,
    )

    # 三次 cost_guard.authorized(orch/audience/creative)+ guardrail.evaluated
    # + campaign.completed == 共 5 条,不应出现 denied / rejected / hitl / replan
    types = [e.event_type for e in store.list_all()]
    assert types == [
        "cost_guard.authorized",
        "cost_guard.authorized",
        "cost_guard.authorized",
        "guardrail.evaluated",
        "campaign.completed",
    ]
    # 所有事件都挂同一个 correlation_id
    assert all(e.correlation_id == CORRELATION for e in store.list_all())
    # 第一个 cost_guard.authorized 在 plan 产出前,campaign_id 必须为 None
    first = store.list_all()[0]
    assert first.campaign_id is None
    assert first.payload["agent_name"] == "orchestrator-agent"
    # 后续事件都带 campaign_id
    assert all(e.campaign_id == CAMPAIGN_ID for e in store.list_all()[1:])


@pytest.mark.asyncio
async def test_event_store_records_guardrail_and_creative_rejected(
    stub_runner_with_reject_variant: list[Any],
) -> None:
    store = InMemoryEventStore()
    with pytest.raises(CreativeRejected):
        await run_campaign(
            brief="x",
            correlation_id=CORRELATION,
            agents=_build_default_agents(),
            event_store=store,
        )

    types = [e.event_type for e in store.list_all()]
    # 最后两条必须是 guardrail.evaluated(decision=reject)+ creative.rejected
    assert types[-2:] == ["guardrail.evaluated", "creative.rejected"]
    evaluated = store.list_by_type("guardrail.evaluated")[0]
    assert evaluated.payload["decision"] == "reject"
    assert "cn_ad_law:absolute_superlatives" in evaluated.payload["violations"]
    rejected = store.list_by_type("creative.rejected")[0]
    assert rejected.payload["subject_id"] == "var:unit:main"
    # campaign.completed 不应该被发出
    assert store.list_by_type("campaign.completed") == []


@pytest.mark.asyncio
async def test_event_store_records_hitl_enqueue(
    stub_runner_with_hitl_variant: list[Any],
) -> None:
    store = InMemoryEventStore()
    queue = InMemoryHitlQueue()
    result = await run_campaign(
        brief="x",
        correlation_id=CORRELATION,
        agents=_build_default_agents(),
        hitl_queue=queue,
        event_store=store,
    )

    assert result.hitl_item is not None
    hitl_events = store.list_by_type("hitl.enqueued")
    assert len(hitl_events) == 1
    assert hitl_events[0].payload["hitl_item_id"] == result.hitl_item.item_id
    assert hitl_events[0].payload["approval_id"] == result.approval.approval_id
    # campaign.completed 仍然发出(needs_hitl 是 coordinator 的正常终态)
    completed = store.list_by_type("campaign.completed")
    assert len(completed) == 1
    assert completed[0].payload["approval_decision"] == "needs_hitl"


@pytest.mark.asyncio
async def test_event_store_records_cost_guard_denial_without_completed(
    stub_runner: list[Any],
) -> None:
    store = InMemoryEventStore()
    strict_guard = CostGuard(CostGuardConfig(1, 1, 1))

    with pytest.raises(CostGuardDenied):
        await run_campaign(
            brief="x",
            correlation_id=CORRELATION,
            agents=_build_default_agents(),
            cost_guard=strict_guard,
            event_store=store,
        )

    types = [e.event_type for e in store.list_all()]
    # Orchestrator 首次 authorize_call 即拒,只应有一条 cost_guard.denied
    assert types == ["cost_guard.denied"]
    denied = store.list_by_type("cost_guard.denied")[0]
    assert denied.payload["agent_name"] == "orchestrator-agent"
    assert denied.campaign_id is None


@pytest.mark.asyncio
async def test_event_store_records_replan_between_attempts(
    stub_runner_for_replan: list[Any],
) -> None:
    store = InMemoryEventStore()
    guard = _DenyAgentFirstTimesCostGuard(deny_agent="audience-agent", deny_times=1)

    await run_campaign(
        brief="初始 brief",
        correlation_id=CORRELATION,
        agents=_build_default_agents(),
        cost_guard=guard,
        event_store=store,
    )

    replan = store.list_by_type("cost_replan.triggered")
    assert len(replan) == 1
    assert replan[0].payload == {
        "denied_agent": "audience-agent",
        "limit_kind": "prompt_tokens",
        "attempt_number": 1,
    }
    # 事件顺序:尝试 1 的 orch authorize → audience denied → replan triggered →
    # 尝试 2 的 orch / audience / creative authorize → guardrail → completed
    types = [e.event_type for e in store.list_all()]
    assert types == [
        "cost_guard.authorized",  # 尝试 1 orch
        "cost_guard.denied",  # 尝试 1 audience 被拒
        "cost_replan.triggered",
        "cost_guard.authorized",  # 尝试 2 orch
        "cost_guard.authorized",  # 尝试 2 audience
        "cost_guard.authorized",  # 尝试 2 creative
        "guardrail.evaluated",
        "campaign.completed",
    ]


@pytest.mark.asyncio
async def test_run_campaign_without_event_store_still_succeeds(
    stub_runner: list[Any],
) -> None:
    """不传 event_store 时 coordinator 必须静默运行,不应 AttributeError。"""
    result = await run_campaign(
        brief="x",
        correlation_id=CORRELATION,
        agents=_build_default_agents(),
    )
    assert result.approval.decision == "approve"
