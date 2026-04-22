"""Guardrail 子系统。

包含:
- `rules`:规则库(品牌词典、广告法规则),Pure Python,便于加载 / 扩展。
- `engine`:规则引擎,吃 CreativeVariant 与 CampaignPlan.brand_guardrails,吐
  ApprovalDecision。不调模型 —— 确定性机器审查优先,P2 再补 LLM 语义审查。

Guardrail agent(接入 Agents SDK)在 P1-052 实现;engine 可独立用在 coordinator 里做
pre-check,保持两条路径(agent 对话式 / 函数式)共用同一套规则。
"""

from auto_marketing_agent.guardrail.engine import (
    GuardrailEngine,
    default_engine,
    evaluate_variant,
)
from auto_marketing_agent.guardrail.rules import (
    BrandDictionary,
    Rule,
    RuleHit,
    RuleSeverity,
    default_rules,
)

__all__ = [
    "BrandDictionary",
    "GuardrailEngine",
    "Rule",
    "RuleHit",
    "RuleSeverity",
    "default_engine",
    "default_rules",
    "evaluate_variant",
]
