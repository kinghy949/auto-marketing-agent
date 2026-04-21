"""v1 模型的业务规则校验测试。

只验证 schema 自身的约束(跨字段一致性、取值域),不验证 agent 业务逻辑。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from auto_marketing_agent.schemas.common import KPITarget, Money
from auto_marketing_agent.schemas.v1 import (
    ApprovalDecision,
    AssetRights,
    AttributionReport,
    AudienceSegment,
    BuyOrder,
    CampaignPlan,
    CreativeAsset,
    CreativeVariant,
    ExperimentArm,
    ExperimentSpec,
    MetricValue,
    StopRule,
)

_NOW = datetime.now(timezone.utc)


def _money(amount: str, currency: str = "USD") -> Money:
    return Money(amount=Decimal(amount), currency=currency)


# --- Money / KPITarget ----------------------------------------------------------


def test_money_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        Money(amount=Decimal("-1"), currency="USD")


def test_money_rejects_lowercase_currency() -> None:
    with pytest.raises(ValidationError):
        Money(amount=Decimal("1"), currency="usd")


# --- CampaignPlan ---------------------------------------------------------------


def _campaign(**overrides: object) -> CampaignPlan:
    base: dict[str, object] = {
        "correlation_id": "corr-1",
        "campaign_id": "camp-1",
        "name": "春季新品",
        "primary_kpi": KPITarget(metric="roas", target=3.0, comparison="gte"),
        "daily_budget": _money("500"),
        "total_budget": _money("15000"),
        "start_date": date(2026, 5, 1),
        "end_date": date(2026, 5, 31),
        "platforms": ["meta"],
        "brand_guardrails": ["最棒", "最便宜"],
    }
    base.update(overrides)
    return CampaignPlan(**base)


def test_campaign_plan_happy_path() -> None:
    plan = _campaign()
    assert plan.schema_name == "campaign_plan"
    assert plan.schema_version == "v1"


def test_campaign_plan_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _campaign(end_date=date(2026, 4, 1))


def test_campaign_plan_rejects_currency_mismatch() -> None:
    with pytest.raises(ValidationError):
        _campaign(total_budget=_money("15000", "EUR"))


def test_campaign_plan_rejects_total_lt_daily() -> None:
    with pytest.raises(ValidationError):
        _campaign(total_budget=_money("100"))


def test_campaign_plan_requires_platform() -> None:
    with pytest.raises(ValidationError):
        _campaign(platforms=[])


# --- AudienceSegment ------------------------------------------------------------


def test_audience_segment_happy_path() -> None:
    seg = AudienceSegment(
        correlation_id="corr-1",
        segment_id="seg-1",
        campaign_id="camp-1",
        name="25-34 女性美妆高频买家",
        size_estimate=120000,
        source="cdp:segment",
    )
    assert seg.hashed_user_ids == []
    assert seg.attributes == {}


def test_audience_segment_rejects_negative_size() -> None:
    with pytest.raises(ValidationError):
        AudienceSegment(
            correlation_id="c",
            segment_id="s",
            campaign_id="c",
            name="x",
            size_estimate=-1,
            source="cdp",
        )


# --- CreativeVariant ------------------------------------------------------------


def _rights() -> AssetRights:
    return AssetRights(licensor="internal", license_id="lic-1")


def test_creative_asset_text_requires_text_only() -> None:
    with pytest.raises(ValidationError):
        CreativeAsset(asset_id="a", asset_type="text", rights=_rights())
    with pytest.raises(ValidationError):
        CreativeAsset(
            asset_id="a",
            asset_type="text",
            text="hi",
            url="https://x",
            rights=_rights(),
        )


def test_creative_asset_image_requires_url_only() -> None:
    with pytest.raises(ValidationError):
        CreativeAsset(asset_id="a", asset_type="image", rights=_rights())
    with pytest.raises(ValidationError):
        CreativeAsset(
            asset_id="a",
            asset_type="image",
            url="https://x",
            text="oops",
            rights=_rights(),
        )


def test_creative_variant_rejects_invalid_language() -> None:
    with pytest.raises(ValidationError):
        CreativeVariant(
            correlation_id="c",
            variant_id="v",
            campaign_id="c",
            target_segment_id="s",
            headline="h",
            body="b",
            call_to_action="go",
            language="chinese",
            generated_by="creative/gpt-4.1",
        )


def test_creative_variant_accepts_bcp47() -> None:
    variant = CreativeVariant(
        correlation_id="c",
        variant_id="v",
        campaign_id="c",
        target_segment_id="s",
        headline="头条",
        body="正文",
        call_to_action="立即购买",
        language="zh-CN",
        generated_by="creative/gpt-4.1",
    )
    assert variant.language == "zh-CN"


# --- BuyOrder -------------------------------------------------------------------


def _buy_order(**overrides: object) -> BuyOrder:
    base: dict[str, object] = {
        "correlation_id": "c",
        "order_id": "o1",
        "campaign_id": "camp-1",
        "idempotency_key": "idk-1",
        "platform": "meta",
        "creative_variant_id": "v1",
        "audience_segment_id": "s1",
        "bid_strategy": "lowest_cost",
        "daily_budget": _money("100"),
        "start_at": _NOW,
        "end_at": _NOW + timedelta(hours=1),
    }
    base.update(overrides)
    return BuyOrder(**base)


def test_buy_order_happy_path() -> None:
    order = _buy_order()
    assert order.idempotency_key == "idk-1"


def test_buy_order_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        _buy_order(end_at=_NOW - timedelta(hours=1))


def test_buy_order_requires_non_empty_idempotency_key() -> None:
    with pytest.raises(ValidationError):
        _buy_order(idempotency_key="")


# --- Experiment -----------------------------------------------------------------


def _arm(arm_id: str, share: float) -> ExperimentArm:
    return ExperimentArm(arm_id=arm_id, buy_order_id=f"bo-{arm_id}", traffic_share=share)


def test_experiment_spec_requires_share_sum_one() -> None:
    with pytest.raises(ValidationError):
        ExperimentSpec(
            correlation_id="c",
            experiment_id="e",
            campaign_id="c",
            primary_metric="roas",
            arms=[_arm("a", 0.4), _arm("b", 0.4)],
            stop_rules=[StopRule(type="fixed_duration", max_duration_hours=48)],
        )


def test_experiment_spec_happy_path() -> None:
    spec = ExperimentSpec(
        correlation_id="c",
        experiment_id="e",
        campaign_id="c",
        primary_metric="roas",
        arms=[_arm("a", 0.5), _arm("b", 0.5)],
        stop_rules=[
            StopRule(type="bayesian_probability", probability_threshold=0.95),
            StopRule(type="min_sample_size", min_samples_per_arm=1000),
        ],
    )
    assert len(spec.arms) == 2


def test_stop_rule_type_and_field_must_align() -> None:
    with pytest.raises(ValidationError):
        StopRule(type="bayesian_probability", max_duration_hours=48)
    with pytest.raises(ValidationError):
        StopRule(type="fixed_duration", probability_threshold=0.95)


def test_stop_rule_requires_its_own_field() -> None:
    with pytest.raises(ValidationError):
        StopRule(type="fixed_duration")


# --- AttributionReport ----------------------------------------------------------


def _metric() -> MetricValue:
    return MetricValue(
        name="roas",
        value=3.2,
        confidence_interval=(2.8, 3.6),
        attribution_method="mmm",
    )


def test_attribution_report_degrades_when_lag_exceeds_slo() -> None:
    with pytest.raises(ValidationError):
        AttributionReport(
            correlation_id="c",
            report_id="r",
            campaign_id="c",
            window_start=_NOW - timedelta(days=1),
            window_end=_NOW,
            data_freshness_lag_hours=8.0,
            is_degraded=False,
            metrics=[_metric()],
        )


def test_attribution_report_happy_path_with_degraded_true() -> None:
    report = AttributionReport(
        correlation_id="c",
        report_id="r",
        campaign_id="c",
        window_start=_NOW - timedelta(days=1),
        window_end=_NOW,
        data_freshness_lag_hours=8.0,
        is_degraded=True,
        metrics=[_metric()],
    )
    assert report.is_degraded


def test_metric_value_rejects_inverted_ci() -> None:
    with pytest.raises(ValidationError):
        MetricValue(
            name="roas",
            value=3.0,
            confidence_interval=(3.5, 2.5),
            attribution_method="mmm",
        )


# --- ApprovalDecision -----------------------------------------------------------


def test_approval_decision_reject_requires_violations() -> None:
    with pytest.raises(ValidationError):
        ApprovalDecision(
            correlation_id="c",
            approval_id="a",
            campaign_id="c",
            subject_type="creative_variant",
            subject_id="v",
            decision="reject",
            rationale="含违禁词",
        )


def test_approval_decision_approve_without_violations_ok() -> None:
    decision = ApprovalDecision(
        correlation_id="c",
        approval_id="a",
        campaign_id="c",
        subject_type="creative_variant",
        subject_id="v",
        decision="approve",
        rationale="符合品牌语调",
    )
    assert decision.violations == []
