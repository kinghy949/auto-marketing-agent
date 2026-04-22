"""JsonlEventStore 单元测试。

覆盖:
- append 写入 JSONL 格式,每行一个 event
- 读路径加载 + occurred_at 升序重排
- 不存在的文件返回空(惰性建)
- 查询方法与 InMemoryEventStore 行为一致
- 解析失败行号可定位
- 未知 schema_version 拒绝反序列化
- 跨进程持久化:两个 store 共享 path 能读到对方写的数据
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from auto_marketing_agent.events import Event, JsonlEventStore

CORRELATION = "corr:jsonl-test"
CAMPAIGN = "cmp:jsonl:001"


def _event(
    event_type: str = "cost_guard.authorized",
    *,
    correlation_id: str = CORRELATION,
    campaign_id: str | None = CAMPAIGN,
    source: str = "coordinator",
    payload: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> Event:
    base = Event(
        event_type=event_type,  # type: ignore[arg-type]
        source=source,
        correlation_id=correlation_id,
        payload=payload or {"k": "v"},
        campaign_id=campaign_id,
    )
    if occurred_at is not None:
        base = replace(base, occurred_at=occurred_at)
    return base


def _at(seconds: int) -> datetime:
    base = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds)


def test_append_and_load_roundtrip(tmp_path: Path) -> None:
    store = JsonlEventStore(path=tmp_path / "events.jsonl")
    e = _event(payload={"agent_name": "orchestrator-agent", "level": "L1"})
    store.append(e)

    loaded = store.list_all()
    assert len(loaded) == 1
    assert loaded[0].event_id == e.event_id
    assert loaded[0].event_type == "cost_guard.authorized"
    assert loaded[0].payload == {"agent_name": "orchestrator-agent", "level": "L1"}
    assert loaded[0].campaign_id == CAMPAIGN
    assert loaded[0].schema_version == "v1"


def test_missing_file_returns_empty_list(tmp_path: Path) -> None:
    store = JsonlEventStore(path=tmp_path / "not-yet.jsonl")
    assert store.list_all() == []
    assert len(store) == 0


def test_append_creates_file_in_existing_parent(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    assert not path.exists()
    store = JsonlEventStore(path=path)
    store.append(_event())
    assert path.exists()
    # 每条 event 一行
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_multiple_appends_serialize_to_separate_lines(tmp_path: Path) -> None:
    store = JsonlEventStore(path=tmp_path / "events.jsonl")
    store.append(_event())
    store.append(_event(event_type="guardrail.evaluated", source="guardrail"))
    store.append(_event(event_type="campaign.completed"))

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all(line.startswith("{") and line.endswith("}") for line in lines)


def test_read_sorts_by_occurred_at_ascending(tmp_path: Path) -> None:
    """写入顺序与 occurred_at 相反时,读侧应按时间升序返回。"""
    store = JsonlEventStore(path=tmp_path / "events.jsonl")
    late = _event(occurred_at=_at(10))
    middle = _event(occurred_at=_at(5))
    early = _event(occurred_at=_at(0))

    store.append(late)
    store.append(middle)
    store.append(early)

    loaded = store.list_all()
    assert [e.occurred_at for e in loaded] == [_at(0), _at(5), _at(10)]


def test_list_by_campaign_and_correlation_and_type(tmp_path: Path) -> None:
    store = JsonlEventStore(path=tmp_path / "events.jsonl")
    e1 = _event(campaign_id="cmp:A", occurred_at=_at(0))
    e2 = _event(campaign_id="cmp:B", occurred_at=_at(5))
    e3 = _event(
        event_type="guardrail.evaluated",
        source="guardrail",
        campaign_id="cmp:A",
        occurred_at=_at(10),
    )
    store.append(e1)
    store.append(e2)
    store.append(e3)

    assert [e.event_id for e in store.list_by_campaign("cmp:A")] == [
        e1.event_id,
        e3.event_id,
    ]
    assert [e.event_id for e in store.list_by_correlation(CORRELATION)] == [
        e1.event_id,
        e2.event_id,
        e3.event_id,
    ]
    assert [e.event_id for e in store.list_by_type("guardrail.evaluated")] == [
        e3.event_id,
    ]


def test_list_by_time_range_half_open(tmp_path: Path) -> None:
    store = JsonlEventStore(path=tmp_path / "events.jsonl")
    store.append(_event(occurred_at=_at(0)))
    mid = _event(occurred_at=_at(5))
    store.append(mid)
    store.append(_event(occurred_at=_at(10)))

    assert [e.event_id for e in store.list_by_time_range(start=_at(5), end=_at(10))] == [
        mid.event_id,
    ]


def test_list_by_time_range_rejects_naive(tmp_path: Path) -> None:
    store = JsonlEventStore(path=tmp_path / "events.jsonl")
    naive = datetime(2026, 4, 22)
    with pytest.raises(ValueError, match="tz-aware"):
        store.list_by_time_range(start=naive)


def test_corrupt_line_raises_with_line_number(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path=path)
    store.append(_event())
    # 手工追加一行损坏数据
    with path.open("a", encoding="utf-8") as f:
        f.write("this-is-not-json\n")
    with pytest.raises(ValueError, match="line 2"):
        store.list_all()


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    # 构造一条 v2 的行,模拟未来升级后的数据误入 v1 代码
    with path.open("w", encoding="utf-8") as f:
        f.write(
            '{"event_id":"evt:x","event_type":"cost_guard.authorized",'
            '"source":"x","schema_version":"v2","correlation_id":"c",'
            '"campaign_id":null,"payload":{},"occurred_at":"2026-04-22T00:00:00+00:00"}\n'
        )
    store = JsonlEventStore(path=path)
    with pytest.raises(ValueError, match="schema_version"):
        store.list_all()


def test_cross_instance_persistence(tmp_path: Path) -> None:
    """两个 store 共享同一个 path,A 写入 B 能读到 —— 证明没有内存缓存。"""
    path = tmp_path / "events.jsonl"
    writer = JsonlEventStore(path=path)
    reader = JsonlEventStore(path=path)

    writer.append(_event(payload={"via": "writer"}))
    loaded = reader.list_all()

    assert len(loaded) == 1
    assert loaded[0].payload == {"via": "writer"}


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path=path)
    store.append(_event())
    # 追加空行(有些编辑器会留空白),不应该崩
    with path.open("a", encoding="utf-8") as f:
        f.write("\n\n")
    store.append(_event(event_type="campaign.completed"))

    assert len(store.list_all()) == 2
