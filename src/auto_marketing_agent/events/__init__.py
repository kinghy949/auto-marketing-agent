"""Event Store —— append-only agent decision 记录(P1-020 / P1-021)。

架构约束(docs/architecture.md §3.6、CLAUDE.md):
- 每个 agent decision 写一条 append-only event,支持重放与审计。
- 反序列化失败 → DLQ(P1-032),不降级为 dict 直接传递。
- 表结构见 `migrations/001_events.sql` 与 ADR-0002。

后端三挡:`InMemoryEventStore`(单测 / CLI demo)、`JsonlEventStore`
(离线重放 / 运维审计)、`PostgresEventStore`(P1-024,生产)。coordinator 依赖
`EventStore` Protocol 而非具体实现,切后端不改上游。`PostgresEventStore` 单独
从 `auto_marketing_agent.events.postgres_store` 导入,避免没装 psycopg 的环境在
import events 时炸;它不在本包的 `__all__`。
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
