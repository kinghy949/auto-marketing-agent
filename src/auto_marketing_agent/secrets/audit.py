"""凭证访问审计。

架构 §3.5 要求"谁、何时、读了哪个 secret"的审计日志。本模块定义最小事件协议,
provider 实现在每次 `get()` 成功 / 拒绝时 emit 一条 `AuditEvent`。

P0 默认绑定 `NullAuditSink`(丢弃);P1 Event Store 就位后,真实 sink 会把事件写入
Event Store,供合规回溯。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """一次 secret 访问的审计记录。"""

    accessor: str
    """访问者标识。Orchestrator 传入,可为 agent 名 / tool 名 / runtime_session_id。"""

    secret_name: str
    outcome: Literal["granted", "not_found", "scope_violation"]
    scoped_view_id: str | None = None
    """若通过 scoped view 访问,记录 view 的标识,便于反查是哪次 handoff 发生的。"""

    timestamp: datetime = field(default_factory=_now_utc)


class AuditSink(Protocol):
    """审计后端接口。实现类必须幂等可重入,不得在 emit 内抛异常(会打断凭证读取)。"""

    def emit(self, event: AuditEvent) -> None: ...


class NullAuditSink:
    """不做任何事的 sink,P0 默认值。"""

    def emit(self, event: AuditEvent) -> None:
        return None
