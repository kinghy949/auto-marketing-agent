"""JSONL 文件后端 event store。

P1-022 重放 CLI 的数据源。每条 event 一行 JSON,append 模式写入;读时整文件 load。
不是生产后端 —— 生产路径走 `PostgresEventStore`(P1-024)。

为什么是 JSONL 而不是 JSON 数组:
- 崩溃时最多丢最后一条未 flush 的行,而数组要整文件重写才能追加。
- 方便 `tail -f` 实时观察、`jq` 流式过滤。

序列化格式:
- `occurred_at` 用 ISO-8601 字符串(UTC),带 `+00:00` 后缀。
- `payload` 原样 JSON-dump;上游保证都是 JSON 可序列化类型(dict/list/str/int/bool/None)。
- `schema_version` 单独落一个 key,方便未来 v2 演进时做路由。

读侧:加载时按 `occurred_at` 升序 re-sort —— 即使写入顺序与自然时间不一致(跨时区
日志合并、历史补录),重放也能给出时序正确的结果。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from auto_marketing_agent.events.event import Event, EventType


def _event_to_dict(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "source": event.source,
        "schema_version": event.schema_version,
        "correlation_id": event.correlation_id,
        "campaign_id": event.campaign_id,
        "payload": event.payload,
        "occurred_at": event.occurred_at.isoformat(),
    }


def _dict_to_event(raw: dict[str, Any]) -> Event:
    """从 JSONL 行还原 Event。

    schema_version 强校验 —— 后续 v2 想要重放 v1 数据必须显式加迁移层,不能默认
    向下兼容(架构决策见 ADR-0002)。
    """
    version = raw.get("schema_version")
    if version != "v1":
        raise ValueError(f"不支持的 schema_version={version!r},当前只接受 v1")
    return Event(
        event_type=cast(EventType, raw["event_type"]),
        source=raw["source"],
        correlation_id=raw["correlation_id"],
        payload=raw["payload"],
        campaign_id=raw.get("campaign_id"),
        event_id=raw["event_id"],
        occurred_at=datetime.fromisoformat(raw["occurred_at"]),
    )


@dataclass(slots=True)
class JsonlEventStore:
    """追加式 JSONL event store。

    `path` 可以不存在 —— 首次 append 时自动创建;父目录必须已存在(避免隐式建目录
    把 typo 写成 `eventsx/` 也 silent 成功)。

    不维护内存缓存:每次读都整文件扫,P1 规模(单 campaign ~15 event,单客户
    ~100K/day)不需要索引。如果这个假设破了,就应该切到 Postgres 后端而不是
    往这里塞 B+ tree。
    """

    path: Path

    def append(self, event: Event) -> None:
        line = json.dumps(_event_to_dict(event), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _load_all(self) -> list[Event]:
        if not self.path.exists():
            return []
        events: list[Event] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(_dict_to_event(json.loads(raw)))
                except (ValueError, KeyError, json.JSONDecodeError) as e:
                    # 文件级故障直接冒泡;DLQ 路径(P1-032)针对运行时反序列化,
                    # 不处理落盘格式错乱 —— 那是运维问题。
                    raise ValueError(
                        f"{self.path}:line {lineno}:解析失败 ({e})"
                    ) from e
        # 写入顺序 ≠ occurred_at 严格升序(Postgres 后端 flush 批次会打乱),
        # 读侧重排一次,下游 caller 不用再 sort。
        events.sort(key=lambda e: e.occurred_at)
        return events

    def list_all(self) -> list[Event]:
        return self._load_all()

    def list_by_campaign(self, campaign_id: str) -> list[Event]:
        return [e for e in self._load_all() if e.campaign_id == campaign_id]

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        return [e for e in self._load_all() if e.correlation_id == correlation_id]

    def list_by_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self._load_all() if e.event_type == event_type]

    def list_by_time_range(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        event_type: EventType | None = None,
    ) -> list[Event]:
        if start is not None and start.tzinfo is None:
            raise ValueError("start 必须是 tz-aware datetime")
        if end is not None and end.tzinfo is None:
            raise ValueError("end 必须是 tz-aware datetime")
        if start is not None and end is not None and start > end:
            raise ValueError(f"start({start}) 不能晚于 end({end})")
        result: list[Event] = []
        for e in self._load_all():
            if start is not None and e.occurred_at < start:
                continue
            if end is not None and e.occurred_at >= end:
                continue
            if event_type is not None and e.event_type != event_type:
                continue
            result.append(e)
        return result

    def __len__(self) -> int:
        return len(self._load_all())
