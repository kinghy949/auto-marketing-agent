"""ApprovalDecision v1 —— Guardrail agent 的审查结论。

Guardrail 既做 **机器审查**(品牌词典 + 法规规则库),也负责把需人工审的内容塞进
HITL 队列(P1-053)。`decision` 字段区分三态:approve / reject / needs_hitl。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from auto_marketing_agent.schemas.base import SchemaEnvelope

ApprovalSubjectType = Literal["creative_variant", "buy_order", "budget_change"]
ApprovalOutcome = Literal["approve", "reject", "needs_hitl"]


class ApprovalDecision(SchemaEnvelope):
    schema_name: Literal["approval_decision"] = "approval_decision"
    schema_version: Literal["v1"] = "v1"

    approval_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    subject_type: ApprovalSubjectType
    subject_id: str = Field(..., min_length=1, description="被审查对象的主键,例:variant_id")
    decision: ApprovalOutcome
    rationale: str = Field(
        ...,
        min_length=1,
        description="给人看的原因说明,即使 approve 也建议填写(利于审计与 Knowledge Store 回写)",
    )
    violations: list[str] = Field(
        default_factory=list,
        description="触发的规则 ID 列表,例:`cn_ad_law:max_superlative`",
    )
    modification_suggestions: list[str] = Field(
        default_factory=list,
        description="修改建议,Creative agent 可据此迭代",
    )
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _check_reject_has_violations(self) -> ApprovalDecision:
        if self.decision == "reject" and not self.violations:
            raise ValueError("decision=reject 时 violations 不能为空")
        return self
