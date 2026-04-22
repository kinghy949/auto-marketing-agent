"""Dead Letter Queue(P1-031)。

架构约束(docs/architecture.md §3.6、CLAUDE.md):
- Schema 反序列化失败 → DLQ(P1-032),不降级为 dict 直接传递。
- Sandbox 崩溃耗尽重试 → DLQ(P1-033),由运维决定是补录还是丢弃。

P1 只实现 `InMemoryDlq`,生产后端(`PostgresDlq`)排 P1-035。coordinator 和
schema registry 依赖 `DlqQueue` Protocol 而非具体实现,切后端不改上游。
"""

from auto_marketing_agent.dlq.queue import (
    DlqAlreadyResolved,
    DlqItem,
    DlqNotFound,
    DlqQueue,
    DlqReason,
    DlqResolution,
    DlqStatus,
    InMemoryDlq,
)

__all__ = [
    "DlqAlreadyResolved",
    "DlqItem",
    "DlqNotFound",
    "DlqQueue",
    "DlqReason",
    "DlqResolution",
    "DlqStatus",
    "InMemoryDlq",
]
