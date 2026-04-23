"""Postgres 后端 DLQ(P1-035)。

与 `PostgresEventStore` 同形制:psycopg3 同步 + `ConnectionPool`,符合
`DlqQueue` Protocol。差异点:

- 不是 append-only —— `resolve` 会 UPDATE 状态。并发安全走 Postgres 行锁 +
  `WHERE status = 'pending'` 条件,同 item 并发 resolve 只有一方能拿到
  `RETURNING` 非空结果,输家分支进入 `DlqAlreadyResolved`。
- `raw_payload` 存 TEXT 而非 JSONB —— 进入 DLQ 通常就是因为反序列化失败,
  JSONB 类型约束会让 INSERT 再失败一次;TEXT 保留原样便于人工 triage。
- 不做自动 TTL / 清理 —— DLQ 堆积正是运维要关注的信号,CLI 层不替他们掩盖。
  归档按 resolved_at 的定期任务留给后续运维脚本。

依赖:`pip install -e '.[postgres]'`。未装时 import 本模块抛 `ModuleNotFoundError`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from auto_marketing_agent.dlq.queue import (
    DlqAlreadyResolved,
    DlqItem,
    DlqNotFound,
    DlqReason,
    DlqResolution,
    DlqStatus,
)

_RESOLVED_RESOLUTIONS: frozenset[str] = frozenset({"replayed", "discarded"})

_INSERT_SQL = """
INSERT INTO dlq_items (
    item_id, reason, source, correlation_id, campaign_id,
    raw_payload, error_message, status, created_at
) VALUES (
    %(item_id)s, %(reason)s, %(source)s, %(correlation_id)s, %(campaign_id)s,
    %(raw_payload)s, %(error_message)s, 'pending', %(created_at)s
)
"""

_SELECT_COLUMNS = (
    "item_id, reason, source, correlation_id, campaign_id, "
    "raw_payload, error_message, status, created_at, "
    "resolved_at, resolver, resolver_note"
)

_UPDATE_RESOLVE_SQL = f"""
UPDATE dlq_items
SET status = %(status)s,
    resolved_at = %(resolved_at)s,
    resolver = %(resolver)s,
    resolver_note = %(resolver_note)s
WHERE item_id = %(item_id)s
  AND status = 'pending'
RETURNING {_SELECT_COLUMNS}
"""


@dataclass(slots=True)
class PostgresDlq:
    """生产 DLQ 后端。

    构造走 `from_dsn`,进程退出前 `close()`。所有 list_* 返回按 `created_at`
    升序,与 `InMemoryDlq` 语义一致。
    """

    pool: ConnectionPool

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> PostgresDlq:
        pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        return cls(pool=pool)

    def close(self) -> None:
        self.pool.close()

    def push(
        self,
        *,
        reason: DlqReason,
        source: str,
        correlation_id: str,
        raw_payload: str,
        error_message: str,
        campaign_id: str | None = None,
    ) -> DlqItem:
        if not source:
            raise ValueError("source 必填:DLQ 记录必须标明失败来源")
        if not correlation_id:
            raise ValueError("correlation_id 必填:DLQ 记录必须可以关联链路")
        if not error_message:
            raise ValueError("error_message 必填:DLQ 记录必须说明失败原因")

        item_id = f"dlq:{uuid.uuid4()}"
        created_at = datetime.now(timezone.utc)
        with self.pool.connection() as conn:
            conn.execute(
                _INSERT_SQL,
                {
                    "item_id": item_id,
                    "reason": reason,
                    "source": source,
                    "correlation_id": correlation_id,
                    "campaign_id": campaign_id,
                    "raw_payload": raw_payload,
                    "error_message": error_message,
                    "created_at": created_at,
                },
            )
        return DlqItem(
            item_id=item_id,
            reason=reason,
            source=source,
            correlation_id=correlation_id,
            campaign_id=campaign_id,
            raw_payload=raw_payload,
            error_message=error_message,
            status="pending",
            created_at=created_at,
        )

    def list_pending(self) -> list[DlqItem]:
        return self._query(
            "WHERE status = 'pending' ORDER BY created_at ASC", ()
        )

    def list_by_reason(self, reason: DlqReason) -> list[DlqItem]:
        return self._query(
            "WHERE reason = %s ORDER BY created_at ASC", (reason,)
        )

    def get(self, item_id: str) -> DlqItem | None:
        rows = self._query("WHERE item_id = %s", (item_id,))
        return rows[0] if rows else None

    def resolve(
        self,
        item_id: str,
        *,
        resolution: DlqResolution,
        resolver: str,
        note: str | None = None,
    ) -> DlqItem:
        """pending → replayed/discarded。并发两路竞争走 UPDATE 行锁 + WHERE 条件,
        输家二次 SELECT 定位当前状态,给调用方正确异常(而不是静默成功)。
        """
        if resolution not in _RESOLVED_RESOLUTIONS:
            raise ValueError(
                f"resolution 必须是终态({sorted(_RESOLVED_RESOLUTIONS)}),收到 {resolution}"
            )
        if not resolver:
            raise ValueError("resolver 必填:DLQ 处置必须有可追溯的操作者")

        params = {
            "item_id": item_id,
            "status": resolution,
            "resolved_at": datetime.now(timezone.utc),
            "resolver": resolver,
            "resolver_note": note,
        }
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_UPDATE_RESOLVE_SQL, params)
            row = cur.fetchone()
            if row is not None:
                return _row_to_item(row)
            # WHERE status='pending' 不匹配:要么根本没这条,要么已 resolve。
            # 二次 SELECT 区分两种情况,给调用方正确异常类型(DlqNotFound vs
            # DlqAlreadyResolved)。同事务内读已读到自己的 UPDATE 影响。
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM dlq_items WHERE item_id = %s",
                (item_id,),
            )
            existing = cur.fetchone()
        if existing is None:
            raise DlqNotFound(item_id)
        raise DlqAlreadyResolved(item_id, cast(DlqStatus, existing["status"]))

    def __len__(self) -> int:
        """全表 count(含已 resolve 的历史项),供运维 / CLI 报数,不走热路径。"""
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM dlq_items")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def _query(self, where: str, params: tuple[Any, ...]) -> list[DlqItem]:
        sql = f"SELECT {_SELECT_COLUMNS} FROM dlq_items {where}"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [_row_to_item(r) for r in rows]


def _row_to_item(row: dict[str, Any]) -> DlqItem:
    return DlqItem(
        item_id=row["item_id"],
        reason=cast(DlqReason, row["reason"]),
        source=row["source"],
        correlation_id=row["correlation_id"],
        campaign_id=row["campaign_id"],
        raw_payload=row["raw_payload"],
        error_message=row["error_message"],
        status=cast(DlqStatus, row["status"]),
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        resolver=row["resolver"],
        resolver_note=row["resolver_note"],
    )
