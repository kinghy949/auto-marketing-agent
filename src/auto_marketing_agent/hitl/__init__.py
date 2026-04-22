"""HITL(Human-in-the-Loop)队列 —— Guardrail `needs_hitl` 决策的暂存层。

架构约束(CLAUDE.md + docs/architecture.md):
- HITL 是事前审批,Circuit Breaker 是事后熔断,两者独立。
- 只有 Guardrail 产出 `needs_hitl` 时才入队;`approve` 直接放行,`reject` 由
  coordinator 抛 `CreativeRejected` 熔断,不走队列。
- P1 只做内存实现,持久化(Postgres / Redis)留 P1-031 Event Store / DLQ 方案统一。
- 队列接口(`HitlQueue`)是 Protocol,coordinator 依赖协议而非实现,方便 P2 切到
  带 DB 后端的版本。
"""

from auto_marketing_agent.hitl.queue import (
    HitlAlreadyResolved,
    HitlItem,
    HitlNotFound,
    HitlQueue,
    HitlStatus,
    InMemoryHitlQueue,
)

__all__ = [
    "HitlAlreadyResolved",
    "HitlItem",
    "HitlNotFound",
    "HitlQueue",
    "HitlStatus",
    "InMemoryHitlQueue",
]
