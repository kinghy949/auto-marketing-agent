"""EventStore 协议与内存实现。

Protocol 暴露"append + 按 key 读 + 按时间窗读"的最小接口:
- 写入:`append(event)` —— 同步,MVP 不做批处理。Postgres 后端接入时改异步并加批量
  flush,由那时的实现自己决定。
- 查询:按 campaign / correlation / type / time_range 四种典型模式。

时间窗查询留给 P1-022 重放 CLI(做"单 campaign 的事件按 occurred_at 升序 dump")
与 P3-011 看板(按 event_type 在窗口内做聚合)使用。所有读接口返回顺序 =
`occurred_at` 升序,caller 不用再 sort。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from auto_marketing_agent.events.event import Event, EventType


class EventStore(Protocol):
    """Append-only event 存储接口。

    实现必须满足:
    - `append` 不阻塞上游业务(失败只能抛异常,不能默默丢 event)。
    - 读接口返回顺序 = `occurred_at` 升序,让 caller 不用自己 sort。
    - 时间窗半开区间 [start, end),与 Postgres/Python 约定对齐;传 None 表示
      单边开。
    """

    def append(self, event: Event) -> None: ...

    def list_by_campaign(self, campaign_id: str) -> list[Event]: ...

    def list_by_correlation(self, correlation_id: str) -> list[Event]: ...

    def list_by_type(self, event_type: EventType) -> list[Event]: ...

    def list_by_time_range(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        event_type: EventType | None = None,
    ) -> list[Event]: ...


@dataclass(slots=True)
class InMemoryEventStore:
    """进程内 event store。只做单元测试与 CLI demo;生产请切 Postgres 后端。

    无锁:P1 coordinator 单 asyncio task,不会并发 append。跨 worker 的持久化由
    `PostgresEventStore`(P1-024)承担,不要给这个类加锁绕过架构决策。
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

    def list_by_time_range(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        event_type: EventType | None = None,
    ) -> list[Event]:
        """半开区间 [start, end) 过滤;`event_type` 可叠加,降低 caller 的二次过滤。

        start/end 必须是 tz-aware —— Event.occurred_at 由 `datetime.now(timezone.utc)`
        生成,naive datetime 比较会抛 TypeError。传 None 表示单边开(便于"只要 N
        秒之后"或"只要 N 秒之前"这类单侧查询)。
        """
        if start is not None and start.tzinfo is None:
            raise ValueError("start 必须是 tz-aware datetime")
        if end is not None and end.tzinfo is None:
            raise ValueError("end 必须是 tz-aware datetime")
        if start is not None and end is not None and start > end:
            raise ValueError(f"start({start}) 不能晚于 end({end})")
        result: list[Event] = []
        for e in self._events:
            if start is not None and e.occurred_at < start:
                continue
            if end is not None and e.occurred_at >= end:
                continue
            if event_type is not None and e.event_type != event_type:
                continue
            result.append(e)
        return result

    def __len__(self) -> int:
        return len(self._events)
