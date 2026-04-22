"""规则库:品牌词典 + 广告法基础规则(P1-050 / P1-051)。

设计取舍:
- 规则用数据驱动,每条 `Rule` 是一个 dataclass。核心行为是 `matches(text)`,由引擎
  层逐个应用到 headline / body / CTA / asset.text。
- 正则或字面量两种匹配方式;正则使用 `re.IGNORECASE` 并在编译时缓存。
- **不做**模糊匹配 / NLP 语义判断 —— 那部分留 P2 接 LLM 二审,这里只守"命中即违规"。
- 三档严重度:
  - `reject`:直接拒(例:绝对化用语、禁用品类)
  - `needs_hitl`:人审(例:疑似医疗宣称)
  - `warn`:记录不影响放行(例:建议改写)

中国广告法 + 欧盟基础规则是起点,不是全集。新增规则应直接在 `DEFAULT_RULES` 里追加,
保持数据和 Python 代码耦合最低。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from re import Pattern
from typing import Literal

RuleSeverity = Literal["reject", "needs_hitl", "warn"]


@dataclass(frozen=True, slots=True)
class Rule:
    """单条规则。

    `patterns` 为正则字符串列表;匹配任意一个即视为命中。`literal_terms` 为不走正则的
    精确子串匹配(IGNORECASE),用于复杂 Unicode / 词形字面量场景,避免正则转义地狱。
    """

    rule_id: str
    description: str
    severity: RuleSeverity
    source: Literal["brand_dict", "cn_ad_law", "eu_ad_basics"]
    patterns: tuple[str, ...] = field(default_factory=tuple)
    literal_terms: tuple[str, ...] = field(default_factory=tuple)
    modification_hint: str = ""


@dataclass(frozen=True, slots=True)
class RuleHit:
    """规则命中记录。引擎把命中打包成 ApprovalDecision.violations。"""

    rule_id: str
    severity: RuleSeverity
    matched_snippet: str
    field_name: str
    modification_hint: str


_COMPILED_CACHE: dict[str, Pattern[str]] = {}


def _compile(pattern: str) -> Pattern[str]:
    cached = _COMPILED_CACHE.get(pattern)
    if cached is None:
        cached = re.compile(pattern, re.IGNORECASE | re.UNICODE)
        _COMPILED_CACHE[pattern] = cached
    return cached


def rule_matches(rule: Rule, text: str) -> str | None:
    """如命中,返回匹配片段;否则返回 None。"""
    if not text:
        return None

    lowered = text.lower()
    for term in rule.literal_terms:
        idx = lowered.find(term.lower())
        if idx >= 0:
            return text[idx : idx + len(term)]

    for pattern in rule.patterns:
        match = _compile(pattern).search(text)
        if match is not None:
            return match.group(0)

    return None


@dataclass(frozen=True, slots=True)
class BrandDictionary:
    """品牌红线词典(P1-050)。

    由 CampaignPlan.brand_guardrails 构造。每个字符串会被转为 `reject` 严重度的字面量
    规则,规则 ID 形如 `brand_dict:<slug>`。大小写不敏感。
    """

    terms: tuple[str, ...]

    @classmethod
    def from_brand_guardrails(cls, guardrails: list[str]) -> BrandDictionary:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in guardrails:
            term = item.strip()
            if not term:
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(term)
        return cls(terms=tuple(cleaned))

    def to_rules(self) -> tuple[Rule, ...]:
        return tuple(
            Rule(
                rule_id=f"brand_dict:{_slug(term)}",
                description=f"品牌禁用词 {term!r}",
                severity="reject",
                source="brand_dict",
                literal_terms=(term,),
                modification_hint=f"删除或替换 {term!r},改用品牌批准的表达",
            )
            for term in self.terms
        )


def _slug(term: str) -> str:
    # 简化 slug:保留 ASCII 字母数字,其余用 `_` 替换,失败时退化成 base36 哈希。
    out_chars: list[str] = []
    for ch in term:
        if ch.isascii() and (ch.isalnum() or ch in ("-", "_")):
            out_chars.append(ch.lower())
        else:
            out_chars.append("_")
    slug = "".join(out_chars).strip("_")
    return slug or f"x{abs(hash(term)):x}"


# 中国《广告法》第九条:绝对化用语(国家级、最高级、最佳等)
_CN_ABSOLUTE_TERMS = Rule(
    rule_id="cn_ad_law:absolute_superlatives",
    description="中国广告法禁止使用'国家级/最高级/最佳'等绝对化用语",
    severity="reject",
    source="cn_ad_law",
    literal_terms=(
        "国家级",
        "最高级",
        "最佳",
        "顶级",
        "独一无二",
        "第一品牌",
        "绝无仅有",
    ),
    modification_hint="改用相对性表述,例:'广受好评'、'业内领先'",
)

# 中国广告法:医疗宣称 / 治疗功效(非药品广告中的"治愈""根治")
_CN_MEDICAL_CLAIMS = Rule(
    rule_id="cn_ad_law:unapproved_medical_claims",
    description="非医疗广告禁止出现治疗 / 治愈 / 根治类宣称",
    severity="needs_hitl",
    source="cn_ad_law",
    literal_terms=("治愈", "根治", "立即见效", "无副作用", "包治"),
    modification_hint="若非药品 / 医疗器械广告,必须删除;若是,需提交人工审核确认资质",
)

# 欧盟消费者保护基础规则:不得做误导性对比与虚假折扣
_EU_MISLEADING_CLAIMS = Rule(
    rule_id="eu_ad_basics:misleading_comparison",
    description="禁止无依据的比较性宣传(例:'便宜 2 倍')",
    severity="needs_hitl",
    source="eu_ad_basics",
    patterns=(r"\b(cheaper|faster|better)\s+than\b",),
    modification_hint="比较必须附数据来源,或改为非比较表述",
)

# 欧盟 / 中国都禁的儿童隐私相关宣称
_CHILD_TARGETING = Rule(
    rule_id="eu_ad_basics:child_targeting_language",
    description="禁止直接诱导儿童购买或施压家长购买",
    severity="reject",
    source="eu_ad_basics",
    literal_terms=("tell your parents to buy", "ask mom to buy"),
    patterns=(r"(?:你的爸妈|你爸妈|叫爸妈)\s*买",),
    modification_hint="移除向儿童的直接购买引导,改为面向家长的表述",
)


DEFAULT_RULES: tuple[Rule, ...] = (
    _CN_ABSOLUTE_TERMS,
    _CN_MEDICAL_CLAIMS,
    _EU_MISLEADING_CLAIMS,
    _CHILD_TARGETING,
)


def default_rules() -> tuple[Rule, ...]:
    """返回不可变的默认规则元组。调用方如需扩展,应拼一个新元组,不修改返回值。"""
    return DEFAULT_RULES
