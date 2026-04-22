"""CLI 测试 —— 用 monkeypatch 截停 agent 执行,验证参数装配与 JSON 输出格式。

不调真实模型,也不加载真实 Settings(通过 env 提供假 OPENAI_API_KEY)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from auto_marketing_agent import __main__ as cli
from auto_marketing_agent.agents.coordinator import CampaignRunResult
from auto_marketing_agent.events import Event, JsonlEventStore
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
    # event_count 出现在 run 子命令输出,因为 CLI 默认传入 InMemoryEventStore。
    # stub 的 run_campaign 不实际 append,所以是 0,但 key 必须在。
    assert set(payload.keys()) == {"plan", "segment", "variant", "approval", "event_count"}
    assert payload["plan"]["campaign_id"] == "cmp:cli:202604"
    assert payload["variant"]["target_segment_id"] == payload["segment"]["segment_id"]
    assert payload["approval"]["decision"] == "approve"
    assert payload["event_count"] == 0


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


# ---------------------------------------------------------------------------
# `run --events-file` —— P1-022 持久化
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_with_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[_CapturedArgs, list[Any]]:
    """run 时把实际传入的 event_store 截出来,让测试能对其 append。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    captured = _CapturedArgs(brief="", correlation_id="", model="")
    stores: list[Any] = []

    def fake_build(model: str) -> Any:
        captured.model = model
        return object()

    async def fake_run_campaign(
        brief: str,
        *,
        correlation_id: str,
        agents: Any,
        event_store: Any = None,
        **_: Any,
    ) -> CampaignRunResult:
        captured.brief = brief
        captured.correlation_id = correlation_id
        stores.append(event_store)
        # 模拟 coordinator 实际 append 一条事件
        if event_store is not None:
            event_store.append(
                Event(
                    event_type="campaign.completed",
                    source="coordinator",
                    correlation_id=correlation_id,
                    payload={"campaign_id": "cmp:cli:202604"},
                    campaign_id="cmp:cli:202604",
                )
            )
        return _fixed_result()

    monkeypatch.setattr(cli, "build_default_agents", fake_build)
    monkeypatch.setattr(cli, "run_campaign", fake_run_campaign)
    return captured, stores


def test_run_with_events_file_persists_jsonl(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    captured_with_events: tuple[_CapturedArgs, list[Any]],
) -> None:
    events_path = tmp_path / "events.jsonl"
    exit_code = cli.main(
        [
            "run",
            "--brief",
            "x",
            "--correlation-id",
            "corr:persist",
            "--events-file",
            str(events_path),
        ]
    )
    assert exit_code == 0
    assert events_path.exists()
    # 从文件重新 load,应能看到那条 campaign.completed
    loaded = JsonlEventStore(path=events_path).list_all()
    assert len(loaded) == 1
    assert loaded[0].event_type == "campaign.completed"
    # stdout 输出里 event_count=1
    out = capsys.readouterr().out
    assert json.loads(out)["event_count"] == 1


def test_run_without_events_file_does_not_create_any(
    tmp_path: Path,
    captured_with_events: tuple[_CapturedArgs, list[Any]],
) -> None:
    # 不传 --events-file:不应在 cwd 或 tmp 造文件
    cli.main(["run", "--brief", "x", "--correlation-id", "corr:mem"])
    # 工作目录里没有 events.jsonl 这种默认名(保证 CLI 不暗写)
    assert not (tmp_path / "events.jsonl").exists()


# ---------------------------------------------------------------------------
# `events replay` —— P1-022
# ---------------------------------------------------------------------------


def _make_events_file(path: Path) -> None:
    """写一个小的 JSONL 固定事件文件,三条事件跨两个 campaign。"""
    store = JsonlEventStore(path=path)
    t0 = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
    from dataclasses import replace

    def evt(
        i: int, *, event_type: str, correlation_id: str, campaign_id: str | None
    ) -> Event:
        base = Event(
            event_type=event_type,  # type: ignore[arg-type]
            source="coordinator",
            correlation_id=correlation_id,
            payload={"i": i},
            campaign_id=campaign_id,
        )
        return replace(base, occurred_at=t0.replace(hour=10 + i))

    store.append(
        evt(0, event_type="cost_guard.authorized", correlation_id="corr:A", campaign_id="cmp:A")
    )
    store.append(
        evt(1, event_type="guardrail.evaluated", correlation_id="corr:A", campaign_id="cmp:A")
    )
    store.append(
        evt(2, event_type="cost_guard.authorized", correlation_id="corr:B", campaign_id="cmp:B")
    )


def test_events_replay_dumps_all_lines_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    _make_events_file(path)
    exit_code = cli.main(["events", "replay", "--events-file", str(path)])
    assert exit_code == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    # 按 occurred_at 升序,第一条 payload.i == 0
    assert json.loads(lines[0])["payload"]["i"] == 0
    assert json.loads(lines[2])["payload"]["i"] == 2


def test_events_replay_filters_by_campaign_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    _make_events_file(path)
    cli.main(["events", "replay", "--events-file", str(path), "--campaign-id", "cmp:A"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["campaign_id"] == "cmp:A" for line in lines)


def test_events_replay_filters_by_event_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    _make_events_file(path)
    cli.main(
        [
            "events",
            "replay",
            "--events-file",
            str(path),
            "--event-type",
            "guardrail.evaluated",
        ]
    )
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "guardrail.evaluated"


def test_events_replay_filters_by_time_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    _make_events_file(path)
    # [11:00, 13:00) 应该只拿到 i=1 的那条(11:00)
    cli.main(
        [
            "events",
            "replay",
            "--events-file",
            str(path),
            "--since",
            "2026-04-22T11:00:00+00:00",
            "--until",
            "2026-04-22T12:00:00+00:00",
        ]
    )
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"]["i"] == 1


def test_events_replay_missing_file_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.jsonl"
    exit_code = cli.main(["events", "replay", "--events-file", str(missing)])
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "不存在" in err


def test_events_replay_rejects_naive_since(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    _make_events_file(path)
    with pytest.raises(SystemExit, match="tz-aware"):
        cli.main(
            [
                "events",
                "replay",
                "--events-file",
                str(path),
                "--since",
                "2026-04-22T00:00:00",
            ]
        )
