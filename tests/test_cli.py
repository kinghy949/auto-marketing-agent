"""CLI 测试 —— 用 monkeypatch 截停 agent 执行,验证参数装配与 JSON 输出格式。

不调真实模型,也不加载真实 Settings(通过 env 提供假 OPENAI_API_KEY)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from auto_marketing_agent import __main__ as cli
from auto_marketing_agent.agents.coordinator import CampaignRunResult
from auto_marketing_agent.schemas.common import KPITarget, Money
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision
from auto_marketing_agent.schemas.v1.audience import AudienceSegment
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan
from auto_marketing_agent.schemas.v1.creative import (
    AssetRights,
    CreativeAsset,
    CreativeVariant,
)


@dataclass
class _CapturedArgs:
    brief: str
    correlation_id: str
    model: str


def _fixed_result() -> CampaignRunResult:
    plan = CampaignPlan(
        correlation_id="corr:cli-test",
        campaign_id="cmp:cli:202604",
        name="CLI 测试",
        primary_kpi=KPITarget(metric="roas", target=3.0, comparison="gte"),
        daily_budget=Money(amount="1000", currency="USD"),
        total_budget=Money(amount="30000", currency="USD"),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 30),
        platforms=["meta"],
    )
    segment = AudienceSegment(
        correlation_id="corr:cli-test",
        segment_id="aud:cli:main",
        campaign_id="cmp:cli:202604",
        name="主受众",
        description="",
        size_estimate=10_000,
        hashed_user_ids=["h:sha256:a"],
        attributes={"geo": "US"},
        source="cdp:segment:cdp:us_young_sporty",
    )
    variant = CreativeVariant(
        correlation_id="corr:cli-test",
        variant_id="var:cli:main",
        campaign_id="cmp:cli:202604",
        target_segment_id="aud:cli:main",
        headline="标题",
        body="正文",
        call_to_action="立即购买",
        language="en-US",
        assets=[
            CreativeAsset(
                asset_id="asset:x",
                asset_type="text",
                text="slogan",
                rights=AssetRights(licensor="internal", license_id="lic-1"),
            )
        ],
        generated_by="creative-agent/gpt-4.1-mini",
    )
    approval = ApprovalDecision(
        correlation_id="corr:cli-test",
        approval_id="apv:var:cli:main:stub",
        campaign_id="cmp:cli:202604",
        subject_type="creative_variant",
        subject_id="var:cli:main",
        decision="approve",
        rationale="unit stub",
    )
    return CampaignRunResult(plan=plan, segment=segment, variant=variant, approval=approval)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> _CapturedArgs:
    """截停 run_campaign 与 build_default_agents,记录调用参数。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    captured = _CapturedArgs(brief="", correlation_id="", model="")

    def fake_build(model: str) -> Any:
        captured.model = model
        return object()

    async def fake_run_campaign(
        brief: str, *, correlation_id: str, agents: Any, **_: Any
    ) -> CampaignRunResult:
        captured.brief = brief
        captured.correlation_id = correlation_id
        return _fixed_result()

    monkeypatch.setattr(cli, "build_default_agents", fake_build)
    monkeypatch.setattr(cli, "run_campaign", fake_run_campaign)
    return captured


def test_run_subcommand_prints_three_payloads_as_json(
    capsys: pytest.CaptureFixture[str], captured: _CapturedArgs
) -> None:
    exit_code = cli.main(
        [
            "run",
            "--brief",
            "美国 18-24 运动人群的 ROAS 活动",
            "--correlation-id",
            "corr:explicit",
            "--model",
            "gpt-4.1-mini",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert set(payload.keys()) == {"plan", "segment", "variant", "approval"}
    assert payload["plan"]["campaign_id"] == "cmp:cli:202604"
    assert payload["variant"]["target_segment_id"] == payload["segment"]["segment_id"]
    assert payload["approval"]["decision"] == "approve"


def test_run_subcommand_generates_correlation_id_when_missing(
    capsys: pytest.CaptureFixture[str], captured: _CapturedArgs
) -> None:
    cli.main(["run", "--brief", "随便"])
    assert captured.correlation_id.startswith("corr:")
    # UUID 部分长度 36(含连字符),加上 `corr:` 共 41
    assert len(captured.correlation_id) == len("corr:") + 36


def test_run_subcommand_forwards_brief_and_model(captured: _CapturedArgs) -> None:
    cli.main(["run", "--brief", "品类:运动鞋", "--model", "gpt-4.1", "--correlation-id", "c1"])
    assert captured.brief == "品类:运动鞋"
    assert captured.model == "gpt-4.1"
    assert captured.correlation_id == "c1"


def test_run_subcommand_requires_brief() -> None:
    with pytest.raises(SystemExit):
        cli.main(["run"])
