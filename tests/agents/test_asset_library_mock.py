"""Asset Library mock 工具的测试。

重点覆盖过滤语义与授权有效性(过期素材不返回)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from auto_marketing_agent.agents.tools.asset_library_mock import (
    build_creative_tools,
    list_mock_assets,
    lookup,
)


def test_lookup_without_filters_returns_all_valid() -> None:
    result = lookup()
    assert len(result) == len(list_mock_assets())  # 当前 mock 里所有素材都未过期


def test_lookup_is_deterministic() -> None:
    a = lookup(theme="sports")
    b = lookup(theme="sports")
    assert a == b


def test_lookup_filters_by_language() -> None:
    result = lookup(language="ja-JP")
    assert len(result) == 1
    assert result[0]["asset_id"] == "asset:gaming_hero_jp_001"


def test_lookup_filters_by_kind() -> None:
    videos = lookup(kind="video")
    assert all(r["kind"] == "video" for r in videos)
    assert len(videos) >= 1


def test_all_returned_assets_have_license() -> None:
    for r in lookup():
        assert r["license_id"], "Asset Library 返回的素材必须带 license_id"
        assert r["licensor"], "Asset Library 返回的素材必须带 licensor"


def test_expired_assets_are_filtered_when_clock_is_past() -> None:
    # 用 mock 里 expires_at=2027-12-31 的 asset 做反向验证:当系统时钟尚未到那天时,
    # 它一定出现在结果里。
    now = datetime.now(timezone.utc)
    result = lookup(theme="sports", kind="video")
    ids = {r["asset_id"] for r in result}
    assert (now < datetime(2027, 12, 31, tzinfo=timezone.utc)) is ("asset:sport_hero_002" in ids)


def test_build_creative_tools_exposes_lookup_tool() -> None:
    tools = build_creative_tools()
    assert len(tools) == 1
    assert tools[0].name == "asset_library_lookup"
