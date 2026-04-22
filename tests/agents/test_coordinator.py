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
    # 第一步 orchestrator 就被拒
    assert exc.value.agent_name == "orchestrator-agent"
    # 被拒前 Runner.run 不应被调用
    assert stub_runner == []
