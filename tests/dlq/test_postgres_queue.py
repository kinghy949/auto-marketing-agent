"""PostgresDlq 集成测试。

与 `tests/events/test_postgres_store.py` 同模式 —— 需要真实 Postgres,
`AMA_TEST_POSTGRES_DSN` 未设时整模块 skip。测试间用 uuid 前缀做 correlation_id
命名空间隔离,不清表。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

pytest.importorskip("psycopg", reason="postgres optional dep 未装,跳过集成测试")

from auto_marketing_agent.dlq import DlqAlreadyResolved, DlqNotFound
from auto_marketing_agent.dlq.postgres_queue import PostgresDlq


@pytest.fixture
def pg_dlq(pg_dsn: str) -> Iterator[PostgresDlq]:
    queue = PostgresDlq.from_dsn(pg_dsn)
    try:
        yield queue
    finally:
        queue.close()


@pytest.fixture
def ns() -> str:
    return f"pg-dlq-{uuid.uuid4()}"


def _push(queue: PostgresDlq, ns: str, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "reason": "schema_deserialization_failed",
        "source": "orchestrator-agent",
        "correlation_id": f"corr:{ns}",
        "raw_payload": '{"bad": true}',
        "error_message": "validation error",
    }
    kwargs.update(overrides)
    return queue.push(**kwargs)  # type: ignore[arg-type]


def test_push_and_get_roundtrip(pg_dlq: PostgresDlq, ns: str) -> None:
    item = _push(pg_dlq, ns, campaign_id="cmp:X")
    assert item.item_id.startswith("dlq:")  # type: ignore[attr-defined]
    assert item.status == "pending"  # type: ignore[attr-defined]
    assert item.campaign_id == "cmp:X"  # type: ignore[attr-defined]

    fetched = pg_dlq.get(item.item_id)  # type: ignore[attr-defined]
    assert fetched is not None
    assert fetched.item_id == item.item_id  # type: ignore[attr-defined]
    assert fetched.created_at.tzinfo is not None  # 读回仍是 tz-aware
    assert fetched.resolved_at is None


def test_list_pending_scoped_to_namespace(pg_dlq: PostgresDlq, ns: str) -> None:
    """list_pending 是全表查询,这里按 correlation_id 过滤自己 namespace 的行。"""
    a = _push(pg_dlq, ns)
    b = _push(pg_dlq, ns, raw_payload='{"other": true}')

    pending = [p for p in pg_dlq.list_pending() if p.correlation_id == f"corr:{ns}"]
    assert [p.item_id for p in pending] == [a.item_id, b.item_id]  # type: ignore[attr-defined]


def test_list_pending_excludes_resolved(pg_dlq: PostgresDlq, ns: str) -> None:
    item = _push(pg_dlq, ns)
    pg_dlq.resolve(item.item_id, resolution="discarded", resolver="alice")  # type: ignore[attr-defined]

    pending = [p for p in pg_dlq.list_pending() if p.correlation_id == f"corr:{ns}"]
    assert pending == []


def test_list_by_reason_filters(pg_dlq: PostgresDlq, ns: str) -> None:
    a = _push(pg_dlq, ns, reason="schema_deserialization_failed")
    b = _push(pg_dlq, ns, reason="sandbox_crash_exhausted_retries")

    schema_items = [
        p
        for p in pg_dlq.list_by_reason("schema_deserialization_failed")
        if p.correlation_id == f"corr:{ns}"
    ]
    assert [p.item_id for p in schema_items] == [a.item_id]  # type: ignore[attr-defined]

    crash_items = [
        p
        for p in pg_dlq.list_by_reason("sandbox_crash_exhausted_retries")
        if p.correlation_id == f"corr:{ns}"
    ]
    assert [p.item_id for p in crash_items] == [b.item_id]  # type: ignore[attr-defined]


def test_resolve_transitions_to_replayed(pg_dlq: PostgresDlq, ns: str) -> None:
    item = _push(pg_dlq, ns)
    resolved = pg_dlq.resolve(
        item.item_id,  # type: ignore[attr-defined]
        resolution="replayed",
        resolver="ops-1",
        note="schema 已回补 v2 字段",
    )
    assert resolved.status == "replayed"
    assert resolved.resolver == "ops-1"
    assert resolved.resolver_note == "schema 已回补 v2 字段"
    assert resolved.resolved_at is not None
    assert resolved.resolved_at.tzinfo is not None


def test_resolve_unknown_raises(pg_dlq: PostgresDlq) -> None:
    with pytest.raises(DlqNotFound):
        pg_dlq.resolve("dlq:does-not-exist", resolution="discarded", resolver="ops")


def test_resolve_already_resolved_raises(pg_dlq: PostgresDlq, ns: str) -> None:
    item = _push(pg_dlq, ns)
    pg_dlq.resolve(item.item_id, resolution="replayed", resolver="ops-1")  # type: ignore[attr-defined]

    with pytest.raises(DlqAlreadyResolved) as exc:
        pg_dlq.resolve(item.item_id, resolution="discarded", resolver="ops-2")  # type: ignore[attr-defined]
    assert exc.value.item_id == item.item_id  # type: ignore[attr-defined]
    assert exc.value.current_status == "replayed"


def test_resolve_rejects_non_terminal(pg_dlq: PostgresDlq) -> None:
    with pytest.raises(ValueError, match="终态"):
        pg_dlq.resolve(
            "dlq:any",
            resolution="pending",  # type: ignore[arg-type]
            resolver="ops",
        )


def test_resolve_requires_resolver(pg_dlq: PostgresDlq, ns: str) -> None:
    item = _push(pg_dlq, ns)
    with pytest.raises(ValueError, match="resolver"):
        pg_dlq.resolve(item.item_id, resolution="replayed", resolver="")  # type: ignore[attr-defined]


def test_push_validation(pg_dlq: PostgresDlq, ns: str) -> None:
    with pytest.raises(ValueError, match="source"):
        _push(pg_dlq, ns, source="")
    with pytest.raises(ValueError, match="correlation_id"):
        pg_dlq.push(
            reason="schema_deserialization_failed",
            source="x",
            correlation_id="",
            raw_payload="{}",
            error_message="x",
        )
    with pytest.raises(ValueError, match="error_message"):
        _push(pg_dlq, ns, error_message="")


def test_len_counts_rows(pg_dlq: PostgresDlq, ns: str) -> None:
    """全表 count,命名空间不缩水 —— 只验增量是否严格 +2。"""
    before = len(pg_dlq)
    _push(pg_dlq, ns)
    _push(pg_dlq, ns)
    assert len(pg_dlq) == before + 2
