"""PostgresEventStore 集成测试。

需要真实 Postgres:`AMA_TEST_POSTGRES_DSN` 未设时整个模块 skip。本地路径:

    docker compose up -d postgres
    export AMA_TEST_POSTGRES_DSN="postgresql://ama:ama@localhost:5432/ama"
    pytest tests/events/test_postgres_store.py

测试之间用 uuid 前缀做 correlation_id / campaign_id 命名空间,表不清空。原因见
tests/conftest.py。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg", reason="postgres optional dep 未装,跳过集成测试")

from auto_marketing_agent.events import Event
from auto_marketing_agent.events.postgres_store import PostgresEventStore


@pytest.fixture
def pg_store(pg_dsn: str) -> Iterator[PostgresEventStore]:
    store = PostgresEventStore.from_dsn(pg_dsn)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def ns() -> str:
    """每个测试独立 correlation / campaign 命名空间,避免并发 / 遗留数据串扰。"""
    return f"pg-test-{uuid.uuid4()}"


def _ev(
    *,
    correlation_id: str,
    event_type: str = "cost_guard.authorized",
    campaign_id: str | None = None,
    payload: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    kwargs: dict[str, object] = {
        "event_type": event_type,
        "source": "test",
        "correlation_id": correlation_id,
        "payload": payload if payload is not None else {"k": "v"},
        "campaign_id": campaign_id,
    }
    if occurred_at is not None:
        kwargs["occurred_at"] = occurred_at
    return Event(**kwargs)  # type: ignore[arg-type]


def test_append_and_list_by_correlation_roundtrip(
    pg_store: PostgresEventStore, ns: str
) -> None:
    corr = f"corr:{ns}"
    e1 = _ev(correlation_id=corr, payload={"n": 1})
    e2 = _ev(correlation_id=corr, event_type="campaign.completed", payload={"n": 2})
    pg_store.append(e1)
    pg_store.append(e2)

    rows = pg_store.list_by_correlation(corr)
    assert [r.event_id for r in rows] == [e1.event_id, e2.event_id]
    # payload 走 JSONB roundtrip 无丢失
    assert rows[0].payload == {"n": 1}
    assert rows[1].payload == {"n": 2}
    # occurred_at 是 tz-aware
    assert rows[0].occurred_at.tzinfo is not None


def test_list_by_campaign_filters_out_other_campaigns(
    pg_store: PostgresEventStore, ns: str
) -> None:
    cmp_a = f"cmp:{ns}:A"
    cmp_b = f"cmp:{ns}:B"
    corr = f"corr:{ns}"
    pg_store.append(_ev(correlation_id=corr, campaign_id=cmp_a, payload={"t": "a"}))
    pg_store.append(_ev(correlation_id=corr, campaign_id=cmp_b, payload={"t": "b"}))
    pg_store.append(_ev(correlation_id=corr, campaign_id=cmp_a, payload={"t": "a2"}))

    rows = pg_store.list_by_campaign(cmp_a)
    assert [r.payload["t"] for r in rows] == ["a", "a2"]


def test_list_by_type_and_order(pg_store: PostgresEventStore, ns: str) -> None:
    corr = f"corr:{ns}"
    # 反序写入,验证读出按 occurred_at 升序
    now = datetime.now(timezone.utc)
    pg_store.append(
        _ev(correlation_id=corr, event_type="hitl.enqueued", occurred_at=now + timedelta(seconds=2))
    )
    pg_store.append(
        _ev(correlation_id=corr, event_type="hitl.enqueued", occurred_at=now + timedelta(seconds=1))
    )

    rows = pg_store.list_by_correlation(corr)
    assert rows[0].occurred_at < rows[1].occurred_at


def test_list_by_time_range_half_open(pg_store: PostgresEventStore, ns: str) -> None:
    corr = f"corr:{ns}"
    t0 = datetime.now(timezone.utc)
    e_before = _ev(correlation_id=corr, payload={"bucket": "before"}, occurred_at=t0)
    e_in = _ev(
        correlation_id=corr,
        payload={"bucket": "in"},
        occurred_at=t0 + timedelta(seconds=5),
    )
    e_after = _ev(
        correlation_id=corr,
        payload={"bucket": "after"},
        occurred_at=t0 + timedelta(seconds=10),
    )
    for e in (e_before, e_in, e_after):
        pg_store.append(e)

    # [t0+1, t0+10) 正好命中 e_in(t0+5),排除 e_before(t0)与 e_after(t0+10,上界开)
    got = pg_store.list_by_time_range(
        start=t0 + timedelta(seconds=1),
        end=t0 + timedelta(seconds=10),
    )
    got_for_corr = [r for r in got if r.correlation_id == corr]
    assert [r.payload["bucket"] for r in got_for_corr] == ["in"]


def test_list_by_time_range_with_event_type(
    pg_store: PostgresEventStore, ns: str
) -> None:
    corr = f"corr:{ns}"
    t0 = datetime.now(timezone.utc)
    pg_store.append(
        _ev(correlation_id=corr, event_type="cost_guard.authorized", occurred_at=t0)
    )
    pg_store.append(
        _ev(correlation_id=corr, event_type="guardrail.evaluated", occurred_at=t0)
    )

    got = pg_store.list_by_time_range(
        start=t0 - timedelta(seconds=1),
        event_type="cost_guard.authorized",
    )
    assert all(r.event_type == "cost_guard.authorized" for r in got)


def test_list_by_time_range_requires_tzaware(pg_store: PostgresEventStore) -> None:
    naive = datetime(2026, 1, 1)
    with pytest.raises(ValueError, match="tz-aware"):
        pg_store.list_by_time_range(start=naive)


def test_append_only_trigger_rejects_update(
    pg_store: PostgresEventStore, ns: str
) -> None:
    """migration 里的 events_reject_mutation 触发器必须真实生效 —— DB 层兜底
    append-only 语义,不能被 UPDATE 绕过。
    """
    import psycopg

    corr = f"corr:{ns}"
    pg_store.append(_ev(correlation_id=corr, payload={"v": 1}))
    with pg_store.pool.connection() as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "UPDATE events SET payload = %s WHERE correlation_id = %s",
                (psycopg.types.json.Jsonb({"v": 2}), corr),
            )


def test_len_counts_rows(pg_store: PostgresEventStore, ns: str) -> None:
    """`__len__` 走 SELECT COUNT(*),不因命名空间隔离而缩水 —— 它是全表 count。
    这里只验证 append 前后数值严格递增,不断言具体值(表可能有前序测试残留)。
    """
    before = len(pg_store)
    pg_store.append(_ev(correlation_id=f"corr:{ns}"))
    pg_store.append(_ev(correlation_id=f"corr:{ns}"))
    after = len(pg_store)
    assert after == before + 2
