"""DLQ 数据结构与内存后端。

`DlqItem` 是一条"无法自动恢复"记录的最小信息集:
- `reason`:失败类别(Literal),决定运维该按哪类 runbook 处置。
- `source`:失败发生在哪个子系统(agent 名、"schema-registry" 等)。
- `raw_payload`:原始字符串(JSON / repr),保留原样以备补录。**不做结构化解析**,
  因为解析失败正是进入 DLQ 的原因,再尝试解析只会二次爆炸。
- `error_message`:触发原因(异常消息 + 简要上下文),够人工 triage 即可。

`InMemoryDlq` 基于 dict 做进程内演示 / 测试。生产后端(`PostgresDlq`)排 P1-035,
coordinator 与 schema registry 依赖 `DlqQueue` Protocol 而非具体实现。

设计取舍:
- `item_id` 用 `dlq:{uuid4}` 随机,不做"按 source + payload hash 幂等"——同一
  payload 多次失败可能代表不同根因(网络抖动 vs 代码 bug),合并会误导运维。
  如果 caller 确实需要幂等,可在入队前自己 dedupe。
- `resolve` 只允许从 pending 出发,重复 resolve 抛 `DlqAlreadyResolved`。理由与
  HITL 队列同:运维改决策应走"重新开单"而不是静默覆盖。
- 不做 TTL / 自动过期。DLQ 的 item 没人看就会堆积,这正是运维要主动关注的信号,
  不是 CLI 该替他们掩盖的。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

# reason 必须是已登记类别,避免 caller 自由发挥导致 runbook 无法覆盖。
# 新增 reason 时同步更新 docs/architecture.md 的 DLQ 分类表。
DlqReason = Literal[
    # Schema 反序列化失败:payload 不符合当前 schema_version 的结构。P1-032 wiring。
    "schema_deserialization_failed",
    # Sandbox 崩溃:重试耗尽,留案。P1-033 wiring,暂未启用。
    "sandbox_crash_exhausted_retries",
]

# 终态命名与 HITL 不同:这里是"处置决定",而非审批结论。
DlqResolution = Literal[
    # replayed:问题已修复,payload 已重新投递/处理。
    "replayed",
    # discarded:payload 无需保留,整条作废(如已过期的实验数据)。
    "discarded",
]

DlqStatus = Literal["pending", "replayed", "discarded"]
_RESOLVED_STATUSES: frozenset[DlqStatus] = frozenset({"replayed", "discarded"})


class DlqNotFound(KeyError):
    """未知 item_id resolve 时抛出。"""


class DlqAlreadyResolved(RuntimeError):
    """已 resolve 的 item 再次 resolve 时抛出。"""

    def __init__(self, item_id: str, current_status: DlqStatus) -> None:
        self.item_id = item_id
        self.current_status = current_status
        super().__init__(f"DLQ item {item_id} 已是 {current_status},不能再次 resolve")


def _new_item_id() -> str:
    return f"dlq:{uuid.uuid4()}"


@dataclass(frozen=True, slots=True)
class DlqItem:
    """单条 DLQ 记录。

    `correlation_id` / `campaign_id` 便于关联 Event Store 时序 —— 运维 triage
    时通常先按 correlation_id 拉全链路事件,再决定怎么处置。
    """

    item_id: str
    reason: DlqReason
    source: str
    correlation_id: str
    raw_payload: str
    error_message: str
    status: DlqStatus
    created_at: datetime
    campaign_id: str | None = None
    resolved_at: datetime | None = None
    resolver: str | None = None
    resolver_note: str | None = None


class DlqQueue(Protocol):
    """DLQ 队列协议。

    上游依赖此协议而非 `InMemoryDlq`,P1-035 切 Postgres 后端不改调用方。
    方法签名刻意小 —— 不暴露内部字典,调用方拿到的都是 `DlqItem` 值对象。
    """

    def push(
        self,
        *,
        reason: DlqReason,
        source: str,
        correlation_id: str,
        raw_payload: str,
        error_message: str,
        campaign_id: str | None = None,
    ) -> DlqItem: ...

    def list_pending(self) -> list[DlqItem]: ...

    def list_by_reason(self, reason: DlqReason) -> list[DlqItem]: ...

    def get(self, item_id: str) -> DlqItem | None: ...

    def resolve(
        self,
        item_id: str,
        *,
        resolution: DlqResolution,
        resolver: str,
        note: str | None = None,
    ) -> DlqItem: ...


@dataclass(slots=True)
class InMemoryDlq:
    """进程内 DLQ。只做演示 / 测试,生产请走 Postgres 后端(P1-035)。

    无锁:P1 coordinator 单 asyncio 线程,不会并发 push。跨 worker 共享由
    `PostgresDlq` 承担,不要给这个类加锁绕过架构决策。
    """

    _items: dict[str, DlqItem] = field(default_factory=dict)

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

        item = DlqItem(
            item_id=_new_item_id(),
            reason=reason,
            source=source,
            correlation_id=correlation_id,
            campaign_id=campaign_id,
            raw_payload=raw_payload,
            error_message=error_message,
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        self._items[item.item_id] = item
        return item

    def list_pending(self) -> list[DlqItem]:
        """按 created_at 升序列出待处置项。"""
        return sorted(
            (i for i in self._items.values() if i.status == "pending"),
            key=lambda i: i.created_at,
        )

    def list_by_reason(self, reason: DlqReason) -> list[DlqItem]:
        return sorted(
            (i for i in self._items.values() if i.reason == reason),
            key=lambda i: i.created_at,
        )

    def get(self, item_id: str) -> DlqItem | None:
        return self._items.get(item_id)

    def resolve(
        self,
        item_id: str,
        *,
        resolution: DlqResolution,
        resolver: str,
        note: str | None = None,
    ) -> DlqItem:
        """pending → replayed/discarded。resolver 必填,留审计链条。"""
        if resolution not in _RESOLVED_STATUSES:
            raise ValueError(
                f"resolution 必须是终态({_RESOLVED_STATUSES}),收到 {resolution}"
            )
        if not resolver:
            raise ValueError("resolver 必填:DLQ 处置必须有可追溯的操作者")

        existing = self._items.get(item_id)
        if existing is None:
            raise DlqNotFound(item_id)
        if existing.status != "pending":
            raise DlqAlreadyResolved(item_id, existing.status)

        resolved = DlqItem(
            item_id=existing.item_id,
            reason=existing.reason,
            source=existing.source,
            correlation_id=existing.correlation_id,
            campaign_id=existing.campaign_id,
            raw_payload=existing.raw_payload,
            error_message=existing.error_message,
            status=resolution,
            created_at=existing.created_at,
            resolved_at=datetime.now(timezone.utc),
            resolver=resolver,
            resolver_note=note,
        )
        self._items[item_id] = resolved
        return resolved

    def __len__(self) -> int:
        return len(self._items)
