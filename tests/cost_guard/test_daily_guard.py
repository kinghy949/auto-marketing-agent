"""DailyCostGuard(L2)单元测试。

覆盖:
- campaign_id=None 直接放行,不查 event store
- 累计 < limit 放行;累计 + planned > limit 抛 CostGuardDenied(L2)
- 同 campaign 的 `cost_guard.authorized` 累加,其他 campaign 不计
- 其他 event_type 不算入(只聚合 authorized)
- UTC 日窗:"昨天"或"未来不该有"的事件不算(通过 list_by_time_range 的 start 过滤)
- 负数 token 直接 ValueError
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from auto_marketing_agent.cost_guard import (
    CostGuardDenied,
    DailyCostConfig,
    DailyCostGuard,
)
from auto_marketing_agent.events import Event, InMemoryEventStore

CAMPAIGN = "cmp:L2-test:1"
OTHER_CAMPAIGN = "cmp:L2-test:2"


def _authorized_event(
    *,
    campaign_id: str | None,
    prompt_tokens: int,
    output_tokens: int,
    occurred_at: datetime | None = None,
) -> Event:
    payload = {
        "agent_name": "audience-agent",
        "level": "L1",
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_output_tokens": output_tokens,
    }
    if occurred_at is None:
        return Event(
            event_type="cost_guard.authorized",
            source="coordinator",
            correlation_id="corr:L2",
            campaign_id=campaign_id,
            payload=payload,
        )
    return Event(
        event_type="cost_guard.authorized",
        source="coordinator",
        correlation_id="corr:L2",
        campaign_id=campaign_id,
        payload=payload,
        occurred_at=occurred_at,
    )


def test_no_campaign_id_is_not_checked() -> None:
    """Orchestrator 阶段 campaign_id 为空,L2 必须直接放行,否则 Orchestrator 跑不起来。"""
    store = InMemoryEventStore()
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=1),
    )
    # 即使 planned 远超 limit,campaign_id=None 也不该抛
    guard.authorize_call(
        agent_name="orchestrator-agent",
        campaign_id=None,
        estimated_prompt_tokens=100_000,
        estimated_output_tokens=100_000,
    )


def test_under_limit_passes() -> None:
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=CAMPAIGN, prompt_tokens=1_000, output_tokens=500))
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        campaign_id=CAMPAIGN,
        estimated_prompt_tokens=2_000,
        estimated_output_tokens=500,
    )


def test_over_limit_denies_with_level_l2() -> None:
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=CAMPAIGN, prompt_tokens=8_000, output_tokens=1_500))
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=10_000),
    )

    with pytest.raises(CostGuardDenied) as exc:
        guard.authorize_call(
            agent_name="audience-agent",
            campaign_id=CAMPAIGN,
            estimated_prompt_tokens=800,
            estimated_output_tokens=200,
        )

    assert exc.value.level == "L2"
    assert exc.value.limit_kind == "campaign_daily_tokens"
    # 已用 8000 + 1500 = 9500,planned 1000,合计 10500 > 10000
    assert exc.value.observed == 10_500
    assert exc.value.limit == 10_000
    assert exc.value.agent_name == "audience-agent"


def test_other_campaigns_are_isolated() -> None:
    """另一个 campaign 花光了预算,不影响本 campaign 放行。"""
    store = InMemoryEventStore()
    store.append(
        _authorized_event(campaign_id=OTHER_CAMPAIGN, prompt_tokens=50_000, output_tokens=50_000)
    )
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        campaign_id=CAMPAIGN,
        estimated_prompt_tokens=1_000,
        estimated_output_tokens=500,
    )


def test_other_event_types_are_ignored() -> None:
    """非 cost_guard.authorized 的事件不算入(哪怕 payload 里有 estimated_*)。"""
    store = InMemoryEventStore()
    noise = Event(
        event_type="guardrail.evaluated",
        source="guardrail",
        correlation_id="corr:L2",
        campaign_id=CAMPAIGN,
        payload={
            "estimated_prompt_tokens": 100_000,
            "estimated_output_tokens": 100_000,
        },
    )
    store.append(noise)
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        campaign_id=CAMPAIGN,
        estimated_prompt_tokens=1_000,
        estimated_output_tokens=500,
    )


def test_yesterday_events_are_outside_window() -> None:
    """UTC 日窗起点 = 今天 00:00,昨天的同 campaign 事件不计入。"""
    store = InMemoryEventStore()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    store.append(
        _authorized_event(
            campaign_id=CAMPAIGN,
            prompt_tokens=50_000,
            output_tokens=50_000,
            occurred_at=yesterday,
        )
    )
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        campaign_id=CAMPAIGN,
        estimated_prompt_tokens=1_000,
        estimated_output_tokens=500,
    )


def test_exact_limit_is_allowed() -> None:
    """边界:累计 + planned == limit,放行(> 才拒)。"""
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=CAMPAIGN, prompt_tokens=5_000, output_tokens=4_000))
    guard = DailyCostGuard(
        event_store=store,
        config=DailyCostConfig(max_tokens_per_campaign_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        campaign_id=CAMPAIGN,
        estimated_prompt_tokens=700,
        estimated_output_tokens=300,
    )


def test_negative_tokens_rejected() -> None:
    store = InMemoryEventStore()
    guard = DailyCostGuard(event_store=store)

    with pytest.raises(ValueError, match="non-negative"):
        guard.authorize_call(
            agent_name="audience-agent",
            campaign_id=CAMPAIGN,
            estimated_prompt_tokens=-1,
            estimated_output_tokens=0,
        )
