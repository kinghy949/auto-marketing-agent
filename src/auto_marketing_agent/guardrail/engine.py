"""规则引擎(P1-051)。

吃 `CreativeVariant` + `CampaignPlan.brand_guardrails`,吐 `ApprovalDecision`。

设计要点:
- **确定性优先**:引擎内部不调 LLM,只按规则表打勾。相同输入必然产出相同决策,
  方便 coordinator 预审与 CI 回归。
- **最坏优先级获胜**:命中任一 `reject` → 直接 reject;否则命中 `needs_hitl` → 走人审;
  都没命中 → approve。`warn` 不改变决策,只进 modification_suggestions。
- **全字段扫描**:headline / body / call_to_action / 每条 text 类 asset 的 text 字段都扫。
  image / video 类 asset 的元数据(URL、license 信息)不在这一层检查,属于 Asset Library
  自身的职责(P2-...).

`evaluate_variant` 是纯函数式接口;`GuardrailEngine` 则封装"带状态的 engine 实例",
在 coordinator / 外部 Guardrail agent 之间复用,避免反复构造规则列表。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from auto_marketing_agent.guardrail.rules import (
    BrandDictionary,
    Rule,
    RuleHit,
    RuleSeverity,
    default_rules,
    rule_matches,
)
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision, ApprovalOutcome
from auto_marketing_agent.schemas.v1.creative import CreativeVariant

_SEVERITY_RANK: dict[RuleSeverity, int] = {"warn": 0, "needs_hitl": 1, "reject": 2}
_RANK_TO_DECISION: dict[int, ApprovalOutcome] = {
    0: "approve",
    1: "needs_hitl",
    2: "reject",
}


@dataclass(frozen=True, slots=True)
class GuardrailEngine:
    """规则引擎。

    `static_rules` 是不依赖具体 campaign 的"法规 / 平台通用红线";每次 evaluate 时
    用 `brand_guardrails` 现场构造 BrandDictionary,叠加到 static_rules 上。这样同一
    引擎实例可以服务任意 campaign,不必为每个 campaign 克隆状态。
    """

    static_rules: tuple[Rule, ...] = field(default_factory=default_rules)

    def evaluate_variant(
        self,
        variant: CreativeVariant,
        *,
        brand_guardrails: list[str],
        correlation_id: str | None = None,
    ) -> ApprovalDecision:
        """评估单条 CreativeVariant。

        `correlation_id` 未指定时沿用 variant 的 correlation_id —— 绝大多数调用路径
        都是同一条链路,多留一个参数只是给 coordinator 在拼接跨 handoff 链路时覆盖用。
        """
        brand_rules = BrandDictionary.from_brand_guardrails(brand_guardrails).to_rules()
        all_rules = (*self.static_rules, *brand_rules)
        hits = _collect_hits(variant, all_rules)

        worst_rank = max((_SEVERITY_RANK[hit.severity] for hit in hits), default=0)
        decision = _RANK_TO_DECISION[worst_rank]

        violations = [hit.rule_id for hit in hits if hit.severity in ("reject", "needs_hitl")]
        suggestions = _dedup_preserve_order(
            hit.modification_hint for hit in hits if hit.modification_hint
        )
        rationale = _build_rationale(decision, hits)

        return ApprovalDecision(
            correlation_id=correlation_id or variant.correlation_id,
            approval_id=_build_approval_id(variant),
            campaign_id=variant.campaign_id,
            subject_type="creative_variant",
            subject_id=variant.variant_id,
            decision=decision,
            rationale=rationale,
            violations=violations,
            modification_suggestions=suggestions,
        )


def default_engine() -> GuardrailEngine:
    """返回装配了 DEFAULT_RULES 的引擎,coordinator 与 agent 路径共享。"""
    return GuardrailEngine()


def evaluate_variant(
    variant: CreativeVariant,
    *,
    brand_guardrails: list[str],
    correlation_id: str | None = None,
) -> ApprovalDecision:
    """函数式入口:临时构造 default_engine 并评估。

    对于一次性调用场景(例:测试、CLI dry-run)比先 `default_engine()` 再调方法更顺手;
    批处理场景应缓存 engine 实例。
    """
    return default_engine().evaluate_variant(
        variant,
        brand_guardrails=brand_guardrails,
        correlation_id=correlation_id,
    )


def _collect_hits(variant: CreativeVariant, rules: tuple[Rule, ...]) -> list[RuleHit]:
    """扫描 variant 的所有用户可见文本字段,返回命中列表。

    同一规则在同一字段里只记录**第一次**命中 —— 重复匹配对决策没有增益,只会放大
    violations / suggestions 噪声。不同字段命中同一规则会各记一条,便于 HITL 理解
    违规分布。
    """
    hits: list[RuleHit] = []
    for field_name, text in _iter_fields(variant):
        seen_in_field: set[str] = set()
        for rule in rules:
            if rule.rule_id in seen_in_field:
                continue
            snippet = rule_matches(rule, text)
            if snippet is None:
                continue
            seen_in_field.add(rule.rule_id)
            hits.append(
                RuleHit(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    matched_snippet=snippet,
                    field_name=field_name,
                    modification_hint=rule.modification_hint,
                )
            )
    return hits


def _iter_fields(variant: CreativeVariant) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = [
        ("headline", variant.headline),
        ("body", variant.body),
        ("call_to_action", variant.call_to_action),
    ]
    for idx, asset in enumerate(variant.assets):
        if asset.asset_type == "text" and asset.text:
            fields.append((f"assets[{idx}].text", asset.text))
    return fields


def _dedup_preserve_order(items: object) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:  # type: ignore[attr-defined]
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _build_rationale(decision: str, hits: list[RuleHit]) -> str:
    """生成给人看的 rationale。

    approve 也填 —— ApprovalDecision schema 允许,但空串会被 min_length=1 打回,
    所以 approve 时给一句固定话术,方便审计流水里识别"机审直通"的记录。
    """
    if not hits:
        return "机审通过:未命中任何规则"

    reasons: list[str] = []
    for hit in hits:
        reasons.append(
            f"[{hit.severity}] {hit.rule_id} 在 {hit.field_name} 命中 {hit.matched_snippet!r}"
        )
    prefix = {
        "approve": "机审通过(含警告)",
        "needs_hitl": "需人工审核",
        "reject": "机审拒绝",
    }[decision]
    return f"{prefix}:" + "; ".join(reasons)


def _build_approval_id(variant: CreativeVariant) -> str:
    """稳定 approval_id。

    用 variant_id + correlation_id 做 hash,保证同一 variant 在同一链路下得到相同的
    approval_id,幂等;不同链路重新审同一 variant 会得到新 ID,便于审计区分。
    """
    raw = f"{variant.variant_id}|{variant.correlation_id}".encode()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"apv:{variant.variant_id}:{digest}"
