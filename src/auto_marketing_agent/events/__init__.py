"""Event Store —— append-only agent decision 记录(P1-020 / P1-021)。

架构约束(docs/architecture.md §3.6、CLAUDE.md):
- 每个 agent decision 写一条 append-only event,支持重放与审计。
- 反序列化失败 → DLQ(P1-032),不降级为 dict 直接传递。
- 表结构见 `migrations/001_events.sql` 与 ADR-0002。

P1 只实现 `InMemoryEventStore`,生产后端(`PostgresEventStore`)排 P2。coordinator
依赖 `EventStore` Protocol 而非具体实现,切后端不改上游。
"""

from auto_marketing_agent.events.event import Event, EventType
from auto_marketing_agent.events.jsonl_store import JsonlEventStore
from auto_marketing_agent.events.store import EventStore, InMemoryEventStore

__all__ = [
    "Event",
    "EventStore",
    "EventType",
    "InMemoryEventStore",
    "JsonlEventStore",
]
