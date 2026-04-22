"""InMemoryDlq 单元测试。

覆盖:
- push:必填字段校验(source / correlation_id / error_message)
- push:新 item_id 随机,不合并同内容 push
- list_pending 按 created_at 升序,resolve 后不再出现
- list_by_reason 过滤 reason
- resolve:pending → replayed/discarded 终态,填 resolver/resolved_at/note
- 未知 item_id resolve 抛 DlqNotFound
- 已 resolve 再 resolve 抛 DlqAlreadyResolved
- 非终态 resolution 拒绝
- resolver 必填
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pytest

from auto_marketing_agent.dlq import (
    DlqAlreadyResolved,
    DlqItem,
    DlqNotFound,
    InMemoryDlq,
)

CORR = "corr:dlq-test"


def _push(queue: InMemoryDlq, **overrides: Any) -> DlqItem:
    kwargs: dict[str, Any] = {
        "reason": "schema_deserialization_failed",
        "source": "orchestrator-agent",
        "correlation_id": CORR,
        "raw_payload": '{"bad": true}',
        "error_message": "validation error: missing 'campaign_id'",
    }
    kwargs.update(overrides)
    return queue.push(**kwargs)


def test_push_creates_pending_item_with_random_id() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    assert item.item_id.startswith("dlq:")
    assert item.status == "pending"
    assert item.reason == "schema_deserialization_failed"
    assert item.source == "orchestrator-agent"
    assert item.correlation_id == CORR
    assert item.campaign_id is None
    assert item.resolved_at is None
    assert item.resolver is None
    assert isinstance(item.created_at, datetime)
    assert len(queue) == 1


def test_push_records_campaign_id_when_given() -> None:
    queue = InMemoryDlq()
    item = _push(queue, campaign_id="cmp:X")
    assert item.campaign_id == "cmp:X"


def test_push_does_not_dedupe_on_identical_payload() -> None:
    queue = InMemoryDlq()
    a = _push(queue)
    b = _push(queue)
    assert a.item_id != b.item_id
    assert len(queue) == 2


def test_push_requires_source() -> None:
    queue = InMemoryDlq()
    with pytest.raises(ValueError, match="source"):
        _push(queue, source="")


def test_push_requires_correlation_id() -> None:
    queue = InMemoryDlq()
    with pytest.raises(ValueError, match="correlation_id"):
        _push(queue, correlation_id="")


def test_push_requires_error_message() -> None:
    queue = InMemoryDlq()
    with pytest.raises(ValueError, match="error_message"):
        _push(queue, error_message="")


def test_list_pending_sorted_by_created_at() -> None:
    queue = InMemoryDlq()
    first = _push(queue, correlation_id="corr:1")
    time.sleep(0.001)
    second = _push(queue, correlation_id="corr:2")

    pending = queue.list_pending()
    assert [i.item_id for i in pending] == [first.item_id, second.item_id]


def test_list_pending_excludes_resolved() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    queue.resolve(item.item_id, resolution="discarded", resolver="alice")
    assert queue.list_pending() == []


def test_list_by_reason_filters_correctly() -> None:
    queue = InMemoryDlq()
    a = _push(queue, reason="schema_deserialization_failed")
    b = _push(queue, reason="sandbox_crash_exhausted_retries")
    c = _push(queue, reason="schema_deserialization_failed")

    schema_items = queue.list_by_reason("schema_deserialization_failed")
    assert [i.item_id for i in schema_items] == [a.item_id, c.item_id]

    crash_items = queue.list_by_reason("sandbox_crash_exhausted_retries")
    assert [i.item_id for i in crash_items] == [b.item_id]


def test_get_returns_item_or_none() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    assert queue.get(item.item_id) is item
    assert queue.get("dlq:does-not-exist") is None


def test_resolve_transitions_to_replayed() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    resolved = queue.resolve(
        item.item_id,
        resolution="replayed",
        resolver="ops-1",
        note="schema 已回补 v2 字段,重放成功",
    )
    assert resolved.status == "replayed"
    assert resolved.resolver == "ops-1"
    assert resolved.resolver_note == "schema 已回补 v2 字段,重放成功"
    assert resolved.resolved_at is not None
    # 存储的是 resolved 版本
    assert queue.get(item.item_id) is resolved


def test_resolve_supports_discarded() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    resolved = queue.resolve(item.item_id, resolution="discarded", resolver="ops-2")
    assert resolved.status == "discarded"


def test_resolve_unknown_item_raises() -> None:
    queue = InMemoryDlq()
    with pytest.raises(DlqNotFound):
        queue.resolve("dlq:nope", resolution="discarded", resolver="ops")


def test_resolve_already_resolved_raises() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    queue.resolve(item.item_id, resolution="replayed", resolver="ops-1")

    with pytest.raises(DlqAlreadyResolved) as exc:
        queue.resolve(item.item_id, resolution="discarded", resolver="ops-2")
    assert exc.value.item_id == item.item_id
    assert exc.value.current_status == "replayed"


def test_resolve_rejects_non_terminal_resolution() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    with pytest.raises(ValueError, match="终态"):
        queue.resolve(item.item_id, resolution="pending", resolver="ops")  # type: ignore[arg-type]


def test_resolve_requires_resolver() -> None:
    queue = InMemoryDlq()
    item = _push(queue)
    with pytest.raises(ValueError, match="resolver"):
        queue.resolve(item.item_id, resolution="replayed", resolver="")
