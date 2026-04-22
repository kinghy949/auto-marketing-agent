"""EventStore 协议与内存实现。

Protocol 只暴露"append + 按 key 读"的最小接口:
- 写入:`append(event)` —— 同步,MVP 不做批处理。Postgres 后端接入时改异步并加批量
  flush,由那时的实现自己决定。
- 查询:按 campaign / correlation / type 三种典型模式。

不暴露"按时间窗查询" —— P1 没这场景,等 P1-022 重放 CLI 或 P3 看板需要再加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from auto_marketing_agent.events.event import Event, EventType


class EventStore(Protocol):
    """Append-only event 存储接口。

    实现必须满足:
    - `append` 不阻塞上游业务(失败只能抛异常,不能默默丢 event)。
    - 读接口返回顺序 = `occurred_at` 升序,让 caller 不用自己 sort。
    """

    def append(self, event: Event) -> None: ...

    def list_by_campaign(self, campaign_id: str) -> list[Event]: ...

    def list_by_correlation(self, correlation_id: str) -> list[Event]: ...

    def list_by_type(self, event_type: EventType) -> list[Event]: ...


@dataclass(slots=True)
class InMemoryEventStore:
    """进程内 event store。只做单元测试与 CLI demo;生产请切 Postgres 后端。

    无锁:P1 coordinator 单 asyncio task,不会并发 append。跨 worker 的持久化由
    `PostgresEventStore`(P2)承担,不要给这个类加锁绕过架构决策。
    """

    _events: list[Event] = field(default_factory=list)

    def append(self, event: Event) -> None:
        self._events.append(event)

    def list_all(self) -> list[Event]:
        """返回目前所有事件的顺序快照,供测试做断言。生产不暴露此方法。"""
        return list(self._events)

    def list_by_campaign(self, campaign_id: str) -> list[Event]:
        return [e for e in self._events if e.campaign_id == campaign_id]

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        return [e for e in self._events if e.correlation_id == correlation_id]

    def list_by_type(self, event_type: EventType) -> list[Event]:
        return [e for e in self._events if e.event_type == event_type]

    def __len__(self) -> int:
        return len(self._events)
