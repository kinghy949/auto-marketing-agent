"""HITL 队列数据结构与内存后端。

`HitlItem` 是审批工单的最小信息集:审批决策(含 rationale / violations / 修改建议)+
被审查的 variant + 状态机字段(pending → approved/rejected/expired)。

`InMemoryHitlQueue` 基于字典实现,只做进程内演示 / 测试。生产场景由 P2 的带 DB
后端的实现接管(届时可能直接落 Event Store,item_id 作为 event 关联键)。

设计取舍:
- `item_id` 用 `hitl:{approval_id}` 派生 —— 既保证幂等入队,又让运维通过
  approval_id 能直达工单。approval_id 已经是 sha256-12 稳定值,不会漂移。
- `resolve` 只允许从 pending 出发,避免重复落地。重复 resolve 抛异常而不是
  幂等返回,因为审批者更换决策是一个需要显式暴露的事件(再审)。
- 队列不带 TTL。过期策略由 caller 调用 `expire_pending` 决定(P1 未启用,留接口)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.creative import CreativeVariant

HitlStatus = Literal["pending", "approved", "rejected", "expired"]
_RESOLVED_STATUSES: frozenset[HitlStatus] = frozenset({"approved", "rejected", "expired"})


class HitlNotFound(KeyError):
    """工单 ID 不存在时抛出。"""


class HitlAlreadyResolved(RuntimeError):
    """工单已 resolve 后再次 resolve 时抛出。

    重复 resolve 视为审批流程异常 —— 审批者改主意应该走另一条"重新开单"流程,
    而不是静默覆盖,避免审计链条断裂。
    """

    def __init__(self, item_id: str, current_status: HitlStatus) -> None:
        self.item_id = item_id
        self.current_status = current_status
        super().__init__(f"HITL item {item_id} 已是 {current_status},不能再次 resolve")


@dataclass(frozen=True, slots=True)
class HitlItem:
    """单条审批工单。

    `brand_guardrails` 从 CampaignPlan 快照下来 —— 审批者需要知道这条 variant 是
    在什么品牌红线下生成的,才能判断 Guardrail 的 rationale 是否合理。
    """

    item_id: str
    approval: ApprovalDecision
    variant: CreativeVariant
    brand_guardrails: tuple[str, ...]
    status: HitlStatus
    created_at: datetime
    decided_at: datetime | None = None
    reviewer: str | None = None
    reviewer_note: str | None = None

    @property
    def correlation_id(self) -> str:
        return self.approval.correlation_id


def _derive_item_id(approval: ApprovalDecision) -> str:
    return f"hitl:{approval.approval_id}"


class HitlQueue(Protocol):
    """审批队列协议。

    coordinator 依赖此协议而非 `InMemoryHitlQueue`,让 P2 切到 DB 后端时不改
    上游代码。方法签名刻意小 —— 不暴露内部字典,调用方拿到的都是 `HitlItem` 值对象。
    """

    def enqueue(
        self,
        *,
        approval: ApprovalDecision,
        variant: CreativeVariant,
        brand_guardrails: tuple[str, ...] = (),
    ) -> HitlItem: ...

    def list_pending(self) -> list[HitlItem]: ...

    def get(self, item_id: str) -> HitlItem | None: ...

    def resolve(
        self,
        item_id: str,
        *,
        status: HitlStatus,
        reviewer: str,
        note: str | None = None,
    ) -> HitlItem: ...


@dataclass(slots=True)
class InMemoryHitlQueue:
    """进程内 HITL 队列。只做演示与测试,生产请走 DB 后端。

    不做线程 / 协程锁 —— P1 的 coordinator 单 asyncio 线程,不会并发 enqueue。
    一旦 P2 需要跨 worker 共享,直接切到带 DB 的实现,不要给这个类加锁。
    """

    _items: dict[str, HitlItem] = field(default_factory=dict)

    def enqueue(
        self,
        *,
        approval: ApprovalDecision,
        variant: CreativeVariant,
        brand_guardrails: tuple[str, ...] = (),
    ) -> HitlItem:
        """幂等入队。

        approval_id 重复时返回已存在的工单,不覆盖 —— coordinator 重试或 replay 时
        不应产出两条工单。如果 variant 被改过再入队,需要先生成新的 approval
        (新的 approval_id),这由 Guardrail 层保证。
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

        item_id = _derive_item_id(approval)
        existing = self._items.get(item_id)
        if existing is not None:
            return existing

        item = HitlItem(
            item_id=item_id,
            approval=approval,
            variant=variant,
            brand_guardrails=tuple(brand_guardrails),
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        self._items[item_id] = item
        return item

    def list_pending(self) -> list[HitlItem]:
        """按 created_at 升序列出待审工单。"""
        return sorted(
            (i for i in self._items.values() if i.status == "pending"),
            key=lambda i: i.created_at,
        )

    def get(self, item_id: str) -> HitlItem | None:
        return self._items.get(item_id)

    def resolve(
        self,
        item_id: str,
        *,
        status: HitlStatus,
        reviewer: str,
        note: str | None = None,
    ) -> HitlItem:
        """将 pending 工单收口为终态。reviewer 必填,供审计链条。"""
        if status not in _RESOLVED_STATUSES:
            raise ValueError(f"status 必须是终态({_RESOLVED_STATUSES}),收到 {status}")
        if not reviewer:
            raise ValueError("reviewer 必填:HITL 决策必须有可追溯的操作者")

        existing = self._items.get(item_id)
        if existing is None:
            raise HitlNotFound(item_id)
        if existing.status != "pending":
            raise HitlAlreadyResolved(item_id, existing.status)

        resolved = HitlItem(
            item_id=existing.item_id,
            approval=existing.approval,
            variant=existing.variant,
            brand_guardrails=existing.brand_guardrails,
            status=status,
            created_at=existing.created_at,
            decided_at=datetime.now(timezone.utc),
            reviewer=reviewer,
            reviewer_note=note,
        )
        self._items[item_id] = resolved
        return resolved

    def __len__(self) -> int:
        return len(self._items)
