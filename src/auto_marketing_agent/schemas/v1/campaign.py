"""CampaignPlan v1 —— Orchestrator agent 的输出 payload。

接收人类输入的 KPI、预算、品牌红线后,Orchestrator 产出一份 `CampaignPlan`,后续所有
agent 的工作都围绕同一个 `campaign_id` 展开(即架构 §5.2 的 Campaign Session 绑定键)。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from auto_marketing_agent.schemas.base import SchemaEnvelope
from auto_marketing_agent.schemas.common import KPITarget, Money, Platform


class CampaignPlan(SchemaEnvelope):
    schema_name: Literal["campaign_plan"] = "campaign_plan"
    schema_version: Literal["v1"] = "v1"

    campaign_id: str = Field(..., min_length=1, description="campaign 唯一标识,全链路锚点")
    name: str = Field(..., min_length=1, description="给人看的名称")
    primary_kpi: KPITarget = Field(..., description="主 KPI,触发 Circuit Breaker 的判定依据")
    daily_budget: Money
    total_budget: Money
    start_date: date
    end_date: date
    platforms: list[Platform] = Field(..., min_length=1, description="投放平台列表,非空")
    brand_guardrails: list[str] = Field(
        default_factory=list,
        description="禁用词 / 禁用品类,供 Guardrail agent 首轮过滤",
    )
    notes: str = Field(default="", description="自由文本备注,不参与自动化决策")

    @model_validator(mode="after")
    def _check_dates_and_budgets(self) -> CampaignPlan:
        if self.end_date < self.start_date:
            raise ValueError("end_date 早于 start_date")
        if self.total_budget.currency != self.daily_budget.currency:
            raise ValueError("total_budget 与 daily_budget 币种必须一致")
        if self.total_budget.amount < self.daily_budget.amount:
            raise ValueError("total_budget 不能小于 daily_budget")
        return self
