"""ExperimentSpec / StopRule v1 —— Experiment agent 的输出。

Experiment agent 负责把同一受众下的多条 creative variant 拆成 A/B 实验,并定义 **事前**
的停止规则。事后由 Attribution agent 根据贝叶斯工具(P2-035)判定是否达到 stop 条件。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_marketing_agent.schemas.base import SchemaEnvelope


class StopRule(BaseModel):
    """单条停止规则。

    三种类型互斥,填写对应字段即可;不填写的字段留空。Experiment agent 可同时挂多条
    stop rule,任一触发即整体停止。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["bayesian_probability", "fixed_duration", "min_sample_size"]
    probability_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="贝叶斯类型:某 arm 胜出概率超过阈值则停止",
    )
    max_duration_hours: int | None = Field(
        default=None,
        gt=0,
        description="固定时长类型:达到时长则停止",
    )
    min_samples_per_arm: int | None = Field(
        default=None,
        gt=0,
        description="最小样本类型:每 arm 至少曝光样本数",
    )

    @model_validator(mode="after")
    def _check_type_field_alignment(self) -> StopRule:
        mapping = {
            "bayesian_probability": self.probability_threshold,
            "fixed_duration": self.max_duration_hours,
            "min_sample_size": self.min_samples_per_arm,
        }
        if mapping[self.type] is None:
            raise ValueError(f"stop rule 类型 {self.type} 必须提供对应字段")
        for other_type, value in mapping.items():
            if other_type != self.type and value is not None:
                raise ValueError(f"stop rule 类型 {self.type} 不应携带 {other_type} 字段")
        return self


class ExperimentArm(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    arm_id: str = Field(..., min_length=1)
    buy_order_id: str = Field(..., min_length=1, description="此 arm 绑定的 BuyOrder")
    traffic_share: float = Field(..., gt=0.0, le=1.0, description="流量占比,0-1")


class ExperimentSpec(SchemaEnvelope):
    schema_name: Literal["experiment_spec"] = "experiment_spec"
    schema_version: Literal["v1"] = "v1"

    experiment_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    primary_metric: Literal["roas", "cac", "ctr", "cvr"] = Field(
        ..., description="判定 arm 胜出的主指标"
    )
    arms: list[ExperimentArm] = Field(..., min_length=2, description="至少两个 arm")
    stop_rules: list[StopRule] = Field(..., min_length=1, description="至少一条停止规则")
    started_at: datetime | None = Field(
        default=None,
        description="实验实际上线时间,未上线时为 None",
    )

    @model_validator(mode="after")
    def _check_traffic_share_sum(self) -> ExperimentSpec:
        total = sum(arm.traffic_share for arm in self.arms)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"所有 arm 的 traffic_share 之和必须等于 1.0,当前 {total}")
        return self
