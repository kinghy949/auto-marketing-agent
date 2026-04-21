"""mock CDP 工具的测试。

覆盖确定性、过滤语义、PII 最小化三条红线。真实 CDP 接入替换时,这组测试迁移到
契约测试层即可,签名保持不变。
"""

from __future__ import annotations

from auto_marketing_agent.agents.tools.cdp_mock import (
    build_cdp_tools,
    list_mock_segments,
    query_segments,
)


def test_query_without_filters_returns_all_segments() -> None:
    result = query_segments()
    assert len(result) == len(list_mock_segments())
    assert {s["segment_id"] for s in result} == {s.segment_id for s in list_mock_segments()}


def test_query_is_deterministic() -> None:
    # 同参数多次调用必须产出完全相同的结果(顺序也一致),
    # 是 golden case 回归的前提。
    first = query_segments({"geo": "US"})
    second = query_segments({"geo": "US"})
    assert first == second


def test_query_filter_by_geo_narrows_results() -> None:
    result = query_segments({"geo": "JP"})
    assert len(result) == 1
    assert result[0]["segment_id"] == "cdp:jp_gamers_premium"


def test_query_filter_compounds_as_and() -> None:
    # geo=US 有两条,再加 age=18-24 只剩一条
    both = query_segments({"geo": "US", "age": "18-24"})
    assert {s["segment_id"] for s in both} == {"cdp:us_young_sporty"}


def test_query_filter_on_unknown_attribute_returns_empty() -> None:
    assert query_segments({"geo": "ZZ"}) == []


def test_hashed_user_ids_only_no_raw_pii() -> None:
    # PII 最小化红线:工具返回的任何 user id 字符串都必须带 `h:` 前缀(代表 hashed)。
    for segment in query_segments():
        ids = segment["hashed_user_ids"]
        assert isinstance(ids, list)
        for uid in ids:
            assert isinstance(uid, str)
            assert uid.startswith("h:"), f"疑似原始 PII 泄漏: {uid!r}"


def test_build_cdp_tools_exposes_query_tool() -> None:
    tools = build_cdp_tools()
    assert len(tools) == 1
    assert tools[0].name == "cdp_query_segments"
