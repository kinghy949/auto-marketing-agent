"""AttributionReport v1 —— Attribution agent 输出的归因报告。

架构 §3.10 明确 freshness SLO:Attribution 数据延迟 > 6h 时必须降级或拒绝出报告,并
联动 Circuit Breaker 暂停出价调整(P2-003)。本 schema 必须携带 `data_freshness_lag_hours`
与 `is_degraded` 两个字段,Orchestrator 据此决定是否信任本次指标。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_marketing_agent.schemas.base import SchemaEnvelope
from auto_marketing_agent.schemas.common import AttributionMethod


class MetricValue(BaseModel):
    """单一指标的归因结果。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["roas", "cac", "ctr", "cvr"]
    value: float
    confidence_interval: tuple[float, float] | None = Field(
        default=None,
        description="95% 置信区间(MMM 输出时必填,MTA 通常无)",
    )
    attribution_method: AttributionMethod

    @model_validator(mode="after")
    def _check_ci_order(self) -> MetricValue:
        if self.confidence_interval is not None:
            low, high = self.confidence_interval
            if low > high:
                raise ValueError("confidence_interval 下界不能大于上界")
        return self


class AttributionReport(SchemaEnvelope):
    schema_name: Literal["attribution_report"] = "attribution_report"
    schema_version: Literal["v1"] = "v1"

    report_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    window_start: datetime
    window_end: datetime
    data_freshness_lag_hours: float = Field(
        ...,
        ge=0.0,
        description="数据最新时间与 window_end 的滞后小时数。> 6h 时 is_degraded 必须为 True。",
    )
    is_degraded: bool = Field(
        ...,
        description="True 表示数据不满足 freshness SLO,下游不应用于出价调整",
    )
    metrics: list[MetricValue] = Field(..., min_length=1, description="campaign 级别汇总指标")
    per_variant: dict[str, list[MetricValue]] = Field(
        default_factory=dict,
        description="key = creative_variant_id,value = 该 variant 的指标集合",
    )
    per_segment: dict[str, list[MetricValue]] = Field(
        default_factory=dict,
        description="key = audience_segment_id,value = 该分群的指标集合",
    )
    notes: str = Field(default="", description="自由文本,例:模型收敛警告、outlier 说明")

    @model_validator(mode="after")
    def _check_window_and_degradation(self) -> AttributionReport:
        if self.window_end <= self.window_start:
            raise ValueError("window_end 必须晚于 window_start")
        if self.data_freshness_lag_hours > 6.0 and not self.is_degraded:
            raise ValueError(
                "data_freshness_lag_hours > 6h 时 is_degraded 必须为 True,"
                "见架构 §3.10 freshness SLO"
            )
        return self
