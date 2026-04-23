"""PlatformDailyCostGuard(L3)单元测试。

覆盖:
- 多 campaign 累加(L3 不按 campaign 过滤,区别于 L2)
- campaign_id=None 的 Orchestrator 事件也算入 L3 聚合
- 累计 < limit 放行;累计 + planned > limit 抛 CostGuardDenied(L3)
- 其他 event_type 不算入(只聚合 authorized)
- UTC 日窗:"昨天"的事件不算
- 边界:累计 + planned == limit 通过
- 负数 token 直接 ValueError
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from auto_marketing_agent.cost_guard import (
    CostGuardDenied,
    PlatformDailyCostConfig,
    PlatformDailyCostGuard,
)
from auto_marketing_agent.events import Event, InMemoryEventStore

CMP_A = "cmp:L3-test:A"
CMP_B = "cmp:L3-test:B"


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
            correlation_id="corr:L3",
            campaign_id=campaign_id,
            payload=payload,
        )
    return Event(
        event_type="cost_guard.authorized",
        source="coordinator",
        correlation_id="corr:L3",
        campaign_id=campaign_id,
        payload=payload,
        occurred_at=occurred_at,
    )


def test_cross_campaign_aggregation() -> None:
    """L3 不按 campaign 过滤:A + B 共享平台预算,累计超限即拒。"""
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=CMP_A, prompt_tokens=4_000, output_tokens=1_000))
    store.append(_authorized_event(campaign_id=CMP_B, prompt_tokens=3_500, output_tokens=1_000))
    guard = PlatformDailyCostGuard(
        event_store=store,
        config=PlatformDailyCostConfig(max_tokens_per_day=10_000),
    )

    with pytest.raises(CostGuardDenied) as exc:
        guard.authorize_call(
            agent_name="creative-agent",
            estimated_prompt_tokens=800,
            estimated_output_tokens=200,
        )

    assert exc.value.level == "L3"
    assert exc.value.limit_kind == "platform_daily_tokens"
    # 4000 + 1000 + 3500 + 1000 = 9500,planned 1000,合计 10500 > 10000
    assert exc.value.observed == 10_500
    assert exc.value.limit == 10_000
    assert exc.value.agent_name == "creative-agent"


def test_orchestrator_events_without_campaign_id_are_counted() -> None:
    """Orchestrator 阶段 campaign_id=None 的事件也要被 L3 算入 —— 它也烧 token。"""
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=None, prompt_tokens=3_000, output_tokens=400))
    store.append(_authorized_event(campaign_id=CMP_A, prompt_tokens=5_000, output_tokens=1_000))
    guard = PlatformDailyCostGuard(
        event_store=store,
        config=PlatformDailyCostConfig(max_tokens_per_day=10_000),
    )

    with pytest.raises(CostGuardDenied) as exc:
        guard.authorize_call(
            agent_name="audience-agent",
            estimated_prompt_tokens=1_000,
            estimated_output_tokens=0,
        )

    # 3000 + 400 + 5000 + 1000 = 9400,planned 1000,合计 10400 > 10000
    assert exc.value.observed == 10_400


def test_under_limit_passes() -> None:
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=CMP_A, prompt_tokens=1_000, output_tokens=500))
    guard = PlatformDailyCostGuard(
        event_store=store,
        config=PlatformDailyCostConfig(max_tokens_per_day=100_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        estimated_prompt_tokens=2_000,
        estimated_output_tokens=500,
    )


def test_other_event_types_are_ignored() -> None:
    """非 cost_guard.authorized 的事件不算入(哪怕 payload 里有 estimated_*)。"""
    store = InMemoryEventStore()
    noise = Event(
        event_type="guardrail.evaluated",
        source="guardrail",
        correlation_id="corr:L3",
        campaign_id=CMP_A,
        payload={
            "estimated_prompt_tokens": 100_000,
            "estimated_output_tokens": 100_000,
        },
    )
    store.append(noise)
    guard = PlatformDailyCostGuard(
        event_store=store,
        config=PlatformDailyCostConfig(max_tokens_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        estimated_prompt_tokens=1_000,
        estimated_output_tokens=500,
    )


def test_yesterday_events_are_outside_window() -> None:
    """UTC 日窗起点 = 今天 00:00,昨天的事件不计入。"""
    store = InMemoryEventStore()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    store.append(
        _authorized_event(
            campaign_id=CMP_A,
            prompt_tokens=50_000,
            output_tokens=50_000,
            occurred_at=yesterday,
        )
    )
    guard = PlatformDailyCostGuard(
        event_store=store,
        config=PlatformDailyCostConfig(max_tokens_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        estimated_prompt_tokens=1_000,
        estimated_output_tokens=500,
    )


def test_exact_limit_is_allowed() -> None:
    """边界:累计 + planned == limit,放行(> 才拒)。"""
    store = InMemoryEventStore()
    store.append(_authorized_event(campaign_id=CMP_A, prompt_tokens=5_000, output_tokens=4_000))
    guard = PlatformDailyCostGuard(
        event_store=store,
        config=PlatformDailyCostConfig(max_tokens_per_day=10_000),
    )

    guard.authorize_call(
        agent_name="audience-agent",
        estimated_prompt_tokens=700,
        estimated_output_tokens=300,
    )


def test_negative_tokens_rejected() -> None:
    store = InMemoryEventStore()
    guard = PlatformDailyCostGuard(event_store=store)

    with pytest.raises(ValueError, match="non-negative"):
        guard.authorize_call(
            agent_name="audience-agent",
            estimated_prompt_tokens=-1,
            estimated_output_tokens=0,
        )
