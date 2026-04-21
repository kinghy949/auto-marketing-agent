"""BuyOrder v1 —— Media Buyer agent 交给平台 API 的投放指令。

`idempotency_key` 是**硬约束**:架构 §4 指出平台 5xx 后重试不能导致重复扣费,所以所有
Media Buyer 操作必须带幂等键,由 Media Buyer agent 在生成 BuyOrder 时填入,Meta Ads /
Google Ads client 透传到平台。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from auto_marketing_agent.schemas.base import SchemaEnvelope
from auto_marketing_agent.schemas.common import BidStrategy, Money, Platform


class BuyOrder(SchemaEnvelope):
    schema_name: Literal["buy_order"] = "buy_order"
    schema_version: Literal["v1"] = "v1"

    order_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(
        ...,
        min_length=1,
        description="防止重试重复扣费的唯一键。同一 (campaign_id, variant_id, "
        "segment_id, 决策时刻) 必须产出相同 key。",
    )
    platform: Platform
    creative_variant_id: str = Field(..., min_length=1)
    audience_segment_id: str = Field(..., min_length=1)
    bid_strategy: BidStrategy
    daily_budget: Money
    start_at: datetime
    end_at: datetime
    approval_id: str | None = Field(
        default=None,
        description="若本单需要 HITL 审批,填入 ApprovalDecision.approval_id;否则为 None",
    )

    @model_validator(mode="after")
    def _check_time_window(self) -> BuyOrder:
        if self.end_at <= self.start_at:
            raise ValueError("end_at 必须晚于 start_at")
        return self
