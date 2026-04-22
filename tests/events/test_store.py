"""InMemoryEventStore 单元测试。

覆盖:
- append + 按 campaign / correlation / event_type / time_range 查询。
- event_id / occurred_at 自动填充,schema_version 固定为 v1。
- Event 不可变(frozen),frozen 修改会抛异常。
- 查询按插入顺序返回(单进程 asyncio 追加顺序 == occurred_at 升序)。
- time_range 半开区间、tz-aware 强制、单边开、type 叠加。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from auto_marketing_agent.events import Event, InMemoryEventStore

CORRELATION = "corr:evt-test"
CAMPAIGN = "cmp:evt:001"


def _event(
    event_type: str = "cost_guard.authorized",
    *,
    correlation_id: str = CORRELATION,
    campaign_id: str | None = CAMPAIGN,
    source: str = "coordinator",
    payload: dict[str, object] | None = None,
) -> Event:
    return Event(
        event_type=event_type,  # type: ignore[arg-type]
        source=source,
        correlation_id=correlation_id,
        payload=payload or {"k": "v"},
        campaign_id=campaign_id,
    )


def test_event_auto_fields_are_populated() -> None:
    evt = _event()
    assert evt.event_id.startswith("evt:")
    assert evt.schema_version == "v1"
    assert evt.occurred_at.tzinfo is not None  # UTC aware


def test_event_is_frozen() -> None:
    evt = _event()
    with pytest.raises(FrozenInstanceError):
        evt.source = "other"  # type: ignore[misc]


def test_append_and_list_all_preserves_order() -> None:
    store = InMemoryEventStore()
    e1 = _event("cost_guard.authorized")
    e2 = _event("guardrail.evaluated", source="guardrail")
    e3 = _event("campaign.completed")

    store.append(e1)
    store.append(e2)
    store.append(e3)

    assert len(store) == 3
    assert store.list_all() == [e1, e2, e3]


def test_list_by_campaign_filters_correctly() -> None:
    store = InMemoryEventStore()
    e1 = _event(campaign_id="cmp:A")
    e2 = _event(campaign_id="cmp:B")
    e3 = _event(campaign_id="cmp:A")

    store.append(e1)
    store.append(e2)
    store.append(e3)

    assert store.list_by_campaign("cmp:A") == [e1, e3]
    assert store.list_by_campaign("cmp:B") == [e2]
    assert store.list_by_campaign("cmp:missing") == []


def test_list_by_campaign_skips_null_campaign_events() -> None:
    """Orchestrator 之前的 cost_guard 事件 campaign_id=None,不应被 cmp:A 查询匹配。"""
    store = InMemoryEventStore()
    e_null = _event(campaign_id=None)
    e_real = _event(campaign_id="cmp:A")

    store.append(e_null)
    store.append(e_real)

    assert store.list_by_campaign("cmp:A") == [e_real]


def test_list_by_correlation_filters_correctly() -> None:
    store = InMemoryEventStore()
    e1 = _event(correlation_id="corr:1")
    e2 = _event(correlation_id="corr:2")
    e3 = _event(correlation_id="corr:1")

    store.append(e1)
    store.append(e2)
    store.append(e3)

    assert store.list_by_correlation("corr:1") == [e1, e3]
    assert store.list_by_correlation("corr:2") == [e2]


def test_list_by_type_filters_correctly() -> None:
    store = InMemoryEventStore()
    e1 = _event("cost_guard.authorized")
    e2 = _event("guardrail.evaluated", source="guardrail")
    e3 = _event("cost_guard.authorized")

    store.append(e1)
    store.append(e2)
    store.append(e3)

    assert store.list_by_type("cost_guard.authorized") == [e1, e3]
    assert store.list_by_type("guardrail.evaluated") == [e2]
    assert store.list_by_type("campaign.completed") == []


def test_empty_store_has_zero_length() -> None:
    store = InMemoryEventStore()
    assert len(store) == 0
    assert store.list_all() == []
    assert store.list_by_campaign("any") == []
    assert store.list_by_correlation("any") == []
    assert store.list_by_type("cost_guard.authorized") == []


def test_each_event_gets_unique_event_id() -> None:
    e1 = _event()
    e2 = _event()
    assert e1.event_id != e2.event_id


# ---------------------------------------------------------------------------
# list_by_time_range(P1-023)
# ---------------------------------------------------------------------------


def _at(seconds: int) -> datetime:
    """生成 2026-04-22T12:00:<seconds>Z 的固定时间,方便测试断言。"""
    base = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds)


def _event_at(when: datetime, *, event_type: str = "cost_guard.authorized") -> Event:
    """用 dataclasses.replace 把 occurred_at 强制改到指定时间(Event 是 frozen)。"""
    return replace(_event(event_type), occurred_at=when)


def test_list_by_time_range_half_open_interval() -> None:
    store = InMemoryEventStore()
    e0 = _event_at(_at(0))
    e5 = _event_at(_at(5))
    e10 = _event_at(_at(10))
    store.append(e0)
    store.append(e5)
    store.append(e10)

    # [t=5, t=10) —— 包含 e5,不含 e10
    result = store.list_by_time_range(start=_at(5), end=_at(10))
    assert result == [e5]


def test_list_by_time_range_unbounded_start() -> None:
    store = InMemoryEventStore()
    e0, e5, e10 = _event_at(_at(0)), _event_at(_at(5)), _event_at(_at(10))
    store.append(e0)
    store.append(e5)
    store.append(e10)

    # 只传 end,包含小于 end 的全部
    assert store.list_by_time_range(end=_at(10)) == [e0, e5]


def test_list_by_time_range_unbounded_end() -> None:
    store = InMemoryEventStore()
    e0, e5, e10 = _event_at(_at(0)), _event_at(_at(5)), _event_at(_at(10))
    store.append(e0)
    store.append(e5)
    store.append(e10)

    # 只传 start,包含大于等于 start 的全部
    assert store.list_by_time_range(start=_at(5)) == [e5, e10]


def test_list_by_time_range_no_bounds_returns_all() -> None:
    store = InMemoryEventStore()
    e0, e5 = _event_at(_at(0)), _event_at(_at(5))
    store.append(e0)
    store.append(e5)

    assert store.list_by_time_range() == [e0, e5]


def test_list_by_time_range_with_event_type_filter() -> None:
    store = InMemoryEventStore()
    a = _event_at(_at(0), event_type="cost_guard.authorized")
    d = _event_at(_at(5), event_type="cost_guard.denied")
    a2 = _event_at(_at(10), event_type="cost_guard.authorized")
    store.append(a)
    store.append(d)
    store.append(a2)

    result = store.list_by_time_range(event_type="cost_guard.authorized")
    assert result == [a, a2]


def test_list_by_time_range_rejects_naive_datetime() -> None:
    store = InMemoryEventStore()
    naive = datetime(2026, 4, 22, 12, 0, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        store.list_by_time_range(start=naive)
    with pytest.raises(ValueError, match="tz-aware"):
        store.list_by_time_range(end=naive)


def test_list_by_time_range_rejects_inverted_range() -> None:
    store = InMemoryEventStore()
    with pytest.raises(ValueError, match="不能晚于"):
        store.list_by_time_range(start=_at(10), end=_at(5))


def test_list_by_time_range_empty_store_returns_empty() -> None:
    store = InMemoryEventStore()
    assert store.list_by_time_range(start=_at(0), end=_at(10)) == []
