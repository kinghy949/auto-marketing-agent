"""跨 schema 复用的基础类型。

只放**非 Envelope** 的辅助类型:金额、平台枚举、KPI 类型等。具体业务 schema 在
`v<N>/` 下按业务概念分文件定义。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["meta", "google", "tiktok"]
"""支持的投放平台。新增平台需同时更新 Media Buyer agent 与 Schema Registry。"""

KPIType = Literal["roas", "cac", "ctr", "cvr"]
"""主 KPI 指标枚举。"""

BidStrategy = Literal[
    "lowest_cost",
    "cost_cap",
    "bid_cap",
    "target_cost",
]
"""Meta Ads 语义的出价策略,P1 接入 Google / TikTok 后可能扩展。"""

AttributionMethod = Literal["mta", "mmm", "blended"]
"""归因方法。架构 §5 明确:iOS 14 后 MTA 不可单独为准,MMM 必备。"""


class Money(BaseModel):
    """金额值对象。

    用 `Decimal` 而不是 `float` 是硬约束 —— 预算与归因金额不允许浮点误差累积。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: Decimal = Field(..., ge=0, description="金额数值,非负")
    currency: str = Field(
        ...,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 三字母货币代码,例:USD / EUR / CNY",
    )


class KPITarget(BaseModel):
    """KPI 目标描述。

    `comparison` 固定为 `gte`/`lte`,因为营销场景只关心"至少达到"或"不超过":
    ROAS 要 `gte`,CAC 要 `lte`。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: KPIType
    target: float = Field(..., description="目标值,例:ROAS 3.0 表示 3 倍回报")
    comparison: Literal["gte", "lte"]
