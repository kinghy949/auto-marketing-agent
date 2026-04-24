"""Postgres 后端 HITL 队列(P1-034)。

与 `PostgresEventStore` / `PostgresDlq` 同形制:psycopg3 同步 +
`ConnectionPool`,符合 `HitlQueue` Protocol。两处关键差异:

- `approval` / `variant` 是 pydantic 模型,完整 dump 成 JSONB 存 —— 审批者
  看到的是"当时的 variant 是什么、Guardrail 的决策是什么",schema 演进
  (v1 → v2)不追溯改写历史工单。读回时用 `model_validate` 重新构造。
- enqueue 幂等走 `INSERT ... ON CONFLICT (item_id) DO NOTHING`:item_id 从
  `approval_id` 派生,天然做幂等主键。RETURNING 为空说明已存在,回 SELECT
  现有行。与 InMemory 一样,不覆盖 —— 再审应该走新 approval_id,不是静默改
  旧工单。

依赖:`pip install -e '.[postgres]'`。未装时 import 本模块抛 `ModuleNotFoundError`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from auto_marketing_agent.hitl.queue import (
    HitlAlreadyResolved,
    HitlItem,
    HitlNotFound,
    HitlStatus,
)
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.creative import CreativeVariant

_RESOLVED_STATUSES: frozenset[str] = frozenset({"approved", "rejected", "expired"})

_INSERT_SQL = """
INSERT INTO hitl_items (
    item_id, correlation_id, approval, variant, brand_guardrails,
    status, created_at
) VALUES (
    %(item_id)s, %(correlation_id)s, %(approval)s, %(variant)s, %(brand_guardrails)s,
    'pending', %(created_at)s
)
ON CONFLICT (item_id) DO NOTHING
"""

_SELECT_COLUMNS = (
    "item_id, correlation_id, approval, variant, brand_guardrails, "
    "status, created_at, decided_at, reviewer, reviewer_note"
)

_UPDATE_RESOLVE_SQL = f"""
UPDATE hitl_items
SET status = %(status)s,
    decided_at = %(decided_at)s,
    reviewer = %(reviewer)s,
    reviewer_note = %(reviewer_note)s
WHERE item_id = %(item_id)s
  AND status = 'pending'
RETURNING {_SELECT_COLUMNS}
"""


@dataclass(slots=True)
class PostgresHitlQueue:
    """生产 HITL 队列后端。语义与 `InMemoryHitlQueue` 对齐,切后端不改上游。"""

    pool: ConnectionPool

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> PostgresHitlQueue:
        pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        return cls(pool=pool)

    def close(self) -> None:
        self.pool.close()

    def enqueue(
        self,
        *,
        approval: ApprovalDecision,
        variant: CreativeVariant,
        brand_guardrails: tuple[str, ...] = (),
    ) -> HitlItem:
        """幂等入队 —— 同一 approval_id 不重复生成工单。

        同 InMemory:只接 decision=needs_hitl;subject_id / variant.variant_id
        必须一致。ON CONFLICT DO NOTHING 命中时二次 SELECT 返回既有行,不
        覆盖 `created_at` 也不改 reviewer —— 审批者改主意应该走重新开单而非
        原地改写(避免审计链断裂)。
        """
        if approval.decision != "needs_hitl":
            raise ValueError(
                f"HITL 队列只接受 decision=needs_hitl,收到 decision={approval.decision}"
            )
        if approval.subject_id != variant.variant_id:
            raise ValueError(
                "approval.subject_id 与 variant.variant_id 不一致:"
                f"{approval.subject_id} vs {variant.variant_id}"
            )

        item_id = f"hitl:{approval.approval_id}"
        created_at = datetime.now(timezone.utc)
        # pydantic 模型 → dict(mode='json' 让 datetime 变 iso 字符串,JSONB 可原样存
        # 且读回时 model_validate 直接吃)。
        approval_dump = approval.model_dump(mode="json")
        variant_dump = variant.model_dump(mode="json")
        guardrails_list = list(brand_guardrails)

        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                _INSERT_SQL,
                {
                    "item_id": item_id,
                    "correlation_id": approval.correlation_id,
                    "approval": Jsonb(approval_dump),
                    "variant": Jsonb(variant_dump),
                    "brand_guardrails": guardrails_list,
                    "created_at": created_at,
                },
            )
            # rowcount == 0 说明 ON CONFLICT 命中,现成工单已存在,直接 SELECT 返回。
            if cur.rowcount == 0:
                cur.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM hitl_items WHERE item_id = %s",
                    (item_id,),
                )
                existing = cur.fetchone()
                if existing is None:
                    # INSERT 说没写、SELECT 又说没有 —— 并发极端情况下的间隙,
                    # 直接报错而不是静默重入,避免调用方拿到"来路不明"的工单。
                    raise RuntimeError(f"enqueue 幂等分支里找不到 item_id={item_id}")
                return _row_to_item(existing)

        return HitlItem(
            item_id=item_id,
            approval=approval,
            variant=variant,
            brand_guardrails=tuple(brand_guardrails),
            status="pending",
            created_at=created_at,
        )

    def list_pending(self) -> list[HitlItem]:
        return self._query(
            "WHERE status = 'pending' ORDER BY created_at ASC", ()
        )

    def get(self, item_id: str) -> HitlItem | None:
        rows = self._query("WHERE item_id = %s", (item_id,))
        return rows[0] if rows else None

    def resolve(
        self,
        item_id: str,
        *,
        status: HitlStatus,
        reviewer: str,
        note: str | None = None,
    ) -> HitlItem:
        """pending → 终态。并发竞争同 PostgresDlq:UPDATE 行锁 + WHERE 条件,
        输家二次 SELECT 区分 `HitlNotFound` vs `HitlAlreadyResolved`。
        """
        if status not in _RESOLVED_STATUSES:
            raise ValueError(
                f"status 必须是终态({sorted(_RESOLVED_STATUSES)}),收到 {status}"
            )
        if not reviewer:
            raise ValueError("reviewer 必填:HITL 决策必须有可追溯的操作者")

        params = {
            "item_id": item_id,
            "status": status,
            "decided_at": datetime.now(timezone.utc),
            "reviewer": reviewer,
            "reviewer_note": note,
        }
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_UPDATE_RESOLVE_SQL, params)
            row = cur.fetchone()
            if row is not None:
                return _row_to_item(row)
            cur.execute(
                f"SELECT {_SELECT_COLUMNS} FROM hitl_items WHERE item_id = %s",
                (item_id,),
            )
            existing = cur.fetchone()
        if existing is None:
            raise HitlNotFound(item_id)
        raise HitlAlreadyResolved(item_id, cast(HitlStatus, existing["status"]))

    def __len__(self) -> int:
        """全表 count(含终态项),供运维 / CLI 报数。"""
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM hitl_items")
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def _query(self, where: str, params: tuple[Any, ...]) -> list[HitlItem]:
        sql = f"SELECT {_SELECT_COLUMNS} FROM hitl_items {where}"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [_row_to_item(r) for r in rows]


def _row_to_item(row: dict[str, Any]) -> HitlItem:
    return HitlItem(
        item_id=row["item_id"],
        approval=ApprovalDecision.model_validate(row["approval"]),
        variant=CreativeVariant.model_validate(row["variant"]),
        brand_guardrails=tuple(row["brand_guardrails"] or ()),
        status=cast(HitlStatus, row["status"]),
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        reviewer=row["reviewer"],
        reviewer_note=row["reviewer_note"],
    )
