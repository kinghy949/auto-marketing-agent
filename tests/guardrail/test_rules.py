"""规则库单测(P1-050)。

只覆盖 `rules` 模块内部:rule_matches 的字面量 / 正则匹配、BrandDictionary 的去重 /
slug 生成。引擎层面的决策融合放到 `test_engine.py`。
"""

from __future__ import annotations

import pytest

from auto_marketing_agent.guardrail.rules import (
    BrandDictionary,
    Rule,
    default_rules,
    rule_matches,
)


def _literal_rule(term: str) -> Rule:
    return Rule(
        rule_id=f"test:literal:{term}",
        description="",
        severity="reject",
        source="brand_dict",
        literal_terms=(term,),
    )


def _pattern_rule(pattern: str) -> Rule:
    return Rule(
        rule_id=f"test:pattern:{pattern}",
        description="",
        severity="reject",
        source="cn_ad_law",
        patterns=(pattern,),
    )


def test_literal_match_is_case_insensitive() -> None:
    rule = _literal_rule("FOO")
    assert rule_matches(rule, "this has foo inside") == "foo"
    assert rule_matches(rule, "THIS HAS FOO") == "FOO"


def test_literal_match_returns_original_case_from_source_text() -> None:
    rule = _literal_rule("最佳")
    assert rule_matches(rule, "我们是最佳选择") == "最佳"


def test_literal_match_misses_when_absent() -> None:
    rule = _literal_rule("顶级")
    assert rule_matches(rule, "我们很努力") is None


def test_pattern_match_supports_regex() -> None:
    rule = _pattern_rule(r"cheaper\s+than")
    assert rule_matches(rule, "We are Cheaper than everyone") == "Cheaper than"


def test_pattern_match_ignorecase_and_unicode() -> None:
    rule = _pattern_rule(r"(?:叫爸妈)\s*买")
    assert rule_matches(rule, "叫爸妈 买这个") == "叫爸妈 买"


def test_empty_text_never_matches() -> None:
    rule = _literal_rule("x")
    assert rule_matches(rule, "") is None


def test_default_rules_cover_cn_absolute_terms() -> None:
    rules = {r.rule_id: r for r in default_rules()}
    assert "cn_ad_law:absolute_superlatives" in rules
    assert rules["cn_ad_law:absolute_superlatives"].severity == "reject"


def test_default_rules_cover_medical_needs_hitl() -> None:
    rules = {r.rule_id: r for r in default_rules()}
    med = rules["cn_ad_law:unapproved_medical_claims"]
    assert med.severity == "needs_hitl"


def test_brand_dictionary_dedupes_and_preserves_first_casing() -> None:
    bd = BrandDictionary.from_brand_guardrails(["赌博", "赌博 ", "赌BO", "Gambling", "gambling"])
    # "赌博" / "赌博 " 去重后保留首次原文;"赌BO" 按小写不同保留
    assert bd.terms == ("赌博", "赌BO", "Gambling")


def test_brand_dictionary_drops_empty_entries() -> None:
    bd = BrandDictionary.from_brand_guardrails(["   ", "", "real"])
    assert bd.terms == ("real",)


def test_brand_dictionary_to_rules_produces_reject_literal_rules() -> None:
    bd = BrandDictionary.from_brand_guardrails(["vape", "Crypto-Scam"])
    rules = bd.to_rules()
    assert {r.rule_id for r in rules} == {"brand_dict:vape", "brand_dict:crypto-scam"}
    for rule in rules:
        assert rule.severity == "reject"
        assert rule.source == "brand_dict"
        assert len(rule.literal_terms) == 1


def test_brand_dictionary_slug_falls_back_to_hash_on_all_non_ascii() -> None:
    bd = BrandDictionary.from_brand_guardrails(["赌博"])
    rule = bd.to_rules()[0]
    # 全 CJK 字符被 slug 成全 `_`,strip 后变空,走哈希 fallback:形如 `brand_dict:x<hex>`
    assert rule.rule_id.startswith("brand_dict:x")


def test_brand_dictionary_ascii_slug_keeps_alnum() -> None:
    bd = BrandDictionary.from_brand_guardrails(["Vape-Pods_42"])
    rule = bd.to_rules()[0]
    assert rule.rule_id == "brand_dict:vape-pods_42"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我们是顶级品牌", "顶级"),
        ("Absolutely the 最佳 choice", "最佳"),
        ("国家级出品", "国家级"),
    ],
)
def test_cn_absolute_terms_rule_fires_on_known_keywords(text: str, expected: str) -> None:
    rule = {r.rule_id: r for r in default_rules()}["cn_ad_law:absolute_superlatives"]
    assert rule_matches(rule, text) == expected
