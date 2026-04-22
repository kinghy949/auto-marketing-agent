"""规则引擎单测(P1-051)。

覆盖三个关键行为:
- 多字段扫描 + 最坏严重度胜出
- BrandDictionary 规则被叠加到 static rules 上
- ApprovalDecision 字段填充正确(approval_id 稳定、violations / suggestions 去重)
"""

from __future__ import annotations

from datetime import datetime, timezone

from auto_marketing_agent.guardrail.engine import (
    GuardrailEngine,
    default_engine,
    evaluate_variant,
)
from auto_marketing_agent.schemas.v1.creative import (
    AssetRights,
    CreativeAsset,
    CreativeVariant,
)

CORRELATION = "corr-guardrail-1"
CAMPAIGN_ID = "cmp:guardrail:202604"


def _variant(
    *,
    headline: str = "清爽春季新品",
    body: str = "适合年轻运动人群,限时七折。",
    call_to_action: str = "立即下单",
    assets: list[CreativeAsset] | None = None,
) -> CreativeVariant:
    return CreativeVariant(
        correlation_id=CORRELATION,
        variant_id="var:guardrail:unit",
        campaign_id=CAMPAIGN_ID,
        target_segment_id="aud:guardrail:unit",
        headline=headline,
        body=body,
        call_to_action=call_to_action,
        language="zh-CN",
        assets=assets
        if assets is not None
        else [
            CreativeAsset(
                asset_id="asset:stub:text",
                asset_type="text",
                text="春日焕新,轻装上阵。",
                rights=AssetRights(licensor="internal", license_id="lic-stub-1"),
            )
        ],
        generated_by="creative-agent/unit",
    )


def test_clean_variant_is_approved() -> None:
    decision = evaluate_variant(_variant(), brand_guardrails=[])
    assert decision.decision == "approve"
    assert decision.violations == []
    assert decision.modification_suggestions == []
    assert "机审通过" in decision.rationale


def test_cn_absolute_term_in_headline_triggers_reject() -> None:
    decision = evaluate_variant(_variant(headline="全网最佳春季新品"), brand_guardrails=[])
    assert decision.decision == "reject"
    assert "cn_ad_law:absolute_superlatives" in decision.violations
    assert decision.modification_suggestions  # 必有至少一条建议


def test_medical_claim_in_body_triggers_needs_hitl() -> None:
    decision = evaluate_variant(_variant(body="使用后立即见效,无副作用。"), brand_guardrails=[])
    assert decision.decision == "needs_hitl"
    assert "cn_ad_law:unapproved_medical_claims" in decision.violations


def test_reject_wins_over_needs_hitl_when_both_fire() -> None:
    decision = evaluate_variant(
        _variant(
            headline="顶级选择,国家级品质",  # reject
            body="立即见效,无副作用。",  # needs_hitl
        ),
        brand_guardrails=[],
    )
    assert decision.decision == "reject"
    # 两类 rule_id 都应出现在 violations 中
    assert "cn_ad_law:absolute_superlatives" in decision.violations
    assert "cn_ad_law:unapproved_medical_claims" in decision.violations


def test_brand_guardrail_term_triggers_reject() -> None:
    decision = evaluate_variant(
        _variant(body="这是一款赌博玩法的休闲游戏"),
        brand_guardrails=["赌博"],
    )
    assert decision.decision == "reject"
    assert any(v.startswith("brand_dict:") for v in decision.violations)


def test_asset_text_is_also_scanned() -> None:
    bad_asset = CreativeAsset(
        asset_id="asset:x",
        asset_type="text",
        text="顶级享受,绝无仅有!",  # 命中绝对化用语
        rights=AssetRights(licensor="internal", license_id="lic-1"),
    )
    decision = evaluate_variant(
        _variant(assets=[bad_asset]),
        brand_guardrails=[],
    )
    assert decision.decision == "reject"


def test_approval_id_stable_for_same_variant_and_correlation() -> None:
    variant = _variant()
    d1 = evaluate_variant(variant, brand_guardrails=[])
    d2 = evaluate_variant(variant, brand_guardrails=[])
    assert d1.approval_id == d2.approval_id


def test_violations_dedupe_across_fields_for_same_rule_with_distinct_hits() -> None:
    """规则在不同字段命中应产生多条违规,但同一规则同一字段只计一次。"""
    decision = evaluate_variant(
        _variant(
            headline="顶级国家级享受",  # 绝对化:两个词同一 rule
            body="继续顶级",  # 绝对化再在 body 出现一次
        ),
        brand_guardrails=[],
    )
    # headline 一次 + body 一次 = 2 条 violations(同 rule_id 在不同字段各记一次)
    occurrences = [v for v in decision.violations if v == "cn_ad_law:absolute_superlatives"]
    assert len(occurrences) == 2


def test_modification_suggestions_are_deduped() -> None:
    decision = evaluate_variant(
        _variant(headline="顶级", body="国家级"),
        brand_guardrails=[],
    )
    # 同一条 modification_hint 出现多次应被合并
    assert len(decision.modification_suggestions) == len(set(decision.modification_suggestions))


def test_correlation_id_override() -> None:
    decision = evaluate_variant(
        _variant(),
        brand_guardrails=[],
        correlation_id="corr-override",
    )
    assert decision.correlation_id == "corr-override"


def test_correlation_id_defaults_to_variant() -> None:
    decision = evaluate_variant(_variant(), brand_guardrails=[])
    assert decision.correlation_id == CORRELATION


def test_default_engine_reuses_default_rules() -> None:
    engine = default_engine()
    assert isinstance(engine, GuardrailEngine)
    assert len(engine.static_rules) >= 4  # 四条默认规则


def test_approval_decision_decided_at_is_utc() -> None:
    decision = evaluate_variant(_variant(), brand_guardrails=[])
    assert decision.decided_at.tzinfo is not None
    # decided_at 应在当前时刻附近;比较保守,只校验 tz 非空 + 不早于 2020
    assert decision.decided_at > datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_child_targeting_rule_fires_on_chinese_phrasing() -> None:
    decision = evaluate_variant(
        _variant(call_to_action="叫爸妈 买这个"),
        brand_guardrails=[],
    )
    assert decision.decision == "reject"
    assert "eu_ad_basics:child_targeting_language" in decision.violations
