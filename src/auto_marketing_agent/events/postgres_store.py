"""Postgres 后端 event store(P1-024)。

按 ADR-0002 的 `events` 表落盘,符合 `EventStore` Protocol 的同步接口。
不引入 asyncpg / 异步层,原因:

- 协议全同步 —— 换异步会连锁修改 DLQ / HITL / 所有调用点;Postgres 的网络往返在
  本地或同 VPC 内通常 < 5ms,P1 规模(单 campaign ~15 event)在 async coordinator
  里阻塞这点时间可接受。
- psycopg3 的同步接口与 asyncio 库写法有差,一旦切异步需要的是"整层换",而不是
  这一个类改几个 await。性能成为瓶颈时再整层切,不超前优化。

连接管理走 `psycopg_pool.ConnectionPool`:长生命周期,应用启动建一次,退出前 close。
调用方用 `with pool.connection() as conn` 借/还,免手动管生命周期。

依赖:`pip install -e '.[postgres]'`。这个可选依赖未装时 import 本模块会抛
`ModuleNotFoundError`;运行时路径不强依赖,`EventStore` Protocol 由内存 /
JSONL 实现满足默认测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from auto_marketing_agent.events.event import Event, EventType

_INSERT_SQL = """
INSERT INTO events (
    event_id, event_type, source, schema_version,
    correlation_id, campaign_id, payload, occurred_at
) VALUES (
    %(event_id)s, %(event_type)s, %(source)s, %(schema_version)s,
    %(correlation_id)s, %(campaign_id)s, %(payload)s, %(occurred_at)s
)
"""

_SELECT_COLUMNS = (
    "event_id, event_type, source, schema_version, "
    "correlation_id, campaign_id, payload, occurred_at"
)


@dataclass(slots=True)
class PostgresEventStore:
    """生产 event store 后端。

    通过 `from_dsn` 建单例,进程退出前调用 `close()` 回收连接池。测试里用
    `yield ... store.close()` 的 fixture 包住。

    所有 list_* 接口返回按 `occurred_at` 升序排好的结果,语义与 `InMemoryEventStore`
    一致 —— 调用方看不到后端差异。
    """

    pool: ConnectionPool

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> PostgresEventStore:
        """DSN 形式:`postgresql://user:pass@host:port/db`。

        `open=True` 让 pool 在构造时即建起最小连接数,便于在测试 fixture 里
        早一点 fail 而不是推迟到第一次请求。生产如需延迟建连改 False。
        """
        pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        return cls(pool=pool)

    def close(self) -> None:
        self.pool.close()

    def append(self, event: Event) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                _INSERT_SQL,
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "source": event.source,
                    "schema_version": event.schema_version,
                    "correlation_id": event.correlation_id,
                    "campaign_id": event.campaign_id,
                    # Jsonb 让 psycopg 用 jsonb 绑定(否则默认 text,DB 侧要额外 cast)。
                    "payload": Jsonb(event.payload),
                    "occurred_at": event.occurred_at,
                },
            )

    def list_by_campaign(self, campaign_id: str) -> list[Event]:
        return self._query("WHERE campaign_id = %s", (campaign_id,))

    def list_by_correlation(self, correlation_id: str) -> list[Event]:
        return self._query("WHERE correlation_id = %s", (correlation_id,))

    def list_by_type(self, event_type: EventType) -> list[Event]:
        return self._query("WHERE event_type = %s", (event_type,))

    def list_by_time_range(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        event_type: EventType | None = None,
    ) -> list[Event]:
        """半开区间 [start, end),tz-aware 必填(与 InMemory / JSONL 后端对齐)。"""
        if start is not None and start.tzinfo is None:
            raise ValueError("start 必须是 tz-aware datetime")
        if end is not None and end.tzinfo is None:
            raise ValueError("end 必须是 tz-aware datetime")
        if start is not None and end is not None and start > end:
            raise ValueError(f"start({start}) 不能晚于 end({end})")

        clauses: list[str] = []
        params: list[Any] = []
        if start is not None:
            clauses.append("occurred_at >= %s")
            params.append(start)
        if end is not None:
            clauses.append("occurred_at < %s")
            params.append(end)
        if event_type is not None:
            clauses.append("event_type = %s")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._query(where, tuple(params))

    def __len__(self) -> int:
        """全表 count。仅供运维 / CLI 报数用,不在查询热路径上。"""
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM events")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def _query(self, where: str, params: tuple[Any, ...]) -> list[Event]:
        sql = f"SELECT {_SELECT_COLUMNS} FROM events {where} ORDER BY occurred_at ASC"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [_row_to_event(r) for r in rows]


def _row_to_event(row: dict[str, Any]) -> Event:
    """强校验 schema_version —— 不支持向下兼容(ADR-0002)。"""
    version = row["schema_version"]
    if version != "v1":
        raise ValueError(f"不支持的 schema_version={version!r},当前只接受 v1")
    return Event(
        event_type=cast(EventType, row["event_type"]),
        source=row["source"],
        correlation_id=row["correlation_id"],
        payload=row["payload"],
        campaign_id=row["campaign_id"],
        event_id=row["event_id"],
        occurred_at=row["occurred_at"],
    )
