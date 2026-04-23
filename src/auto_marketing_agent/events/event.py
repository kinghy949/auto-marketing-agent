"""Event 值对象与 event_type 常量。

`Event` 是不可变、自带 `event_id` / `occurred_at` 的记录。payload 用
`dict[str, Any]`,不同 event_type 的 shape 由发射方约定(见 `coordinator._emit_event`
以及本文件 event_type 注释)。升级 payload 结构必须 bump `schema_version`,不能
向下兼容 —— append-only 的事件一旦落库不可改。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

# 目前 8 个 event_type,全部由 coordinator / guardrail / hitl / dlq 发射。
# 新增类型必须:
# 1. 在这里添加 Literal
# 2. 在相应 emit 处写 payload 约定(哪些 key 必填、值的类型)
# 3. 更新 ADR-0002 的事件集清单
EventType = Literal[
    # cost_guard.authorized: Cost Guard L1 放行。payload 含 agent_name / level /
    # estimated_prompt_tokens / estimated_output_tokens。
    "cost_guard.authorized",
    # cost_guard.denied: Cost Guard L1 拒绝。payload 含 agent_name / level /
    # limit_kind / observed / limit。
    "cost_guard.denied",
    # cost_replan.triggered: coordinator 触发重规划。payload 含 denied_agent /
    # limit_kind / attempt_number。
    "cost_replan.triggered",
    # guardrail.evaluated: 机审完成(不论 approve / reject / needs_hitl)。payload 含
    # subject_id / decision / violations。
    "guardrail.evaluated",
    # creative.rejected: Guardrail 硬拒,coordinator 抛出 CreativeRejected 前发射。
    # payload 含 subject_id / violations。
    "creative.rejected",
    # hitl.enqueued: 工单入 HITL 队列。payload 含 hitl_item_id / approval_id。
    "hitl.enqueued",
    # campaign.completed: 一次 run_campaign 成功终止。payload 含 campaign_id /
    # segment_id / variant_id / approval_decision。
    "campaign.completed",
    # dlq.enqueued: 不可恢复失败入 DLQ(P1-032 schema 反序列化失败等)。payload 含
    # dlq_item_id / reason / source / error_message。
    "dlq.enqueued",
]


def _new_event_id() -> str:
    return f"evt:{uuid.uuid4()}"


@dataclass(frozen=True, slots=True)
class Event:
    """单条 append-only 事件。

    field 顺序:必填的业务语义字段在前,自动填充字段在后。`payload` 由调用方构造,
    不给默认值,避免误发空 payload。`campaign_id` 可空,因为 Orchestrator 返回前
    的事件(如 Orchestrator 自身的 cost_guard.authorized)还没有 campaign_id。
    """

    event_type: EventType
    source: str
    correlation_id: str
    payload: dict[str, Any]
    campaign_id: str | None = None
    event_id: str = field(default_factory=_new_event_id)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: Literal["v1"] = "v1"
