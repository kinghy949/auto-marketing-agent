"""Mock CDP 工具。

提供一组可被 Audience agent 调用的确定性查询接口,替代真实 CDP 接入前的占位。核心设计:

- **纯函数 + 冻结数据**:同样的入参永远返回同样的结果,供 golden case 用 snapshot 对比。
- **只暴露 hashed user IDs**:对齐架构 §7 PII 最小化。此处写死的 "哈希值" 只是示例字符串,
  并非真实 SHA-256,但类型契约与线上一致 —— 后续替换真实 CDP 时,Audience agent 无需改动。
- **属性维度有限枚举**:`age`、`geo`、`interest`、`device`、`tier` 五个维度,够覆盖回归测试。

工具函数通过 `agents.function_tool` 装饰器暴露给 LLM。入参全部为基础类型,schema 由
SDK 从类型注解自动推导,不需要额外维护 JSON schema。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents import FunctionTool, function_tool

AttributeKey = Literal["age", "geo", "interest", "device", "tier"]


@dataclass(frozen=True, slots=True)
class MockCdpSegment:
    """CDP 中一条候选分群的静态视图。

    字段刻意保持最小:Audience agent 拿到这些后会决定是否纳入输出,再自行构造
    `AudienceSegment` schema。工具层不直接产出最终 schema,职责清晰。
    """

    segment_id: str
    name: str
    size_estimate: int
    attributes: dict[str, str]
    hashed_user_ids: tuple[str, ...]


_MOCK_SEGMENTS: tuple[MockCdpSegment, ...] = (
    MockCdpSegment(
        segment_id="cdp:us_young_sporty",
        name="US 18-24 运动爱好者",
        size_estimate=420_000,
        attributes={"geo": "US", "age": "18-24", "interest": "sports", "device": "mobile"},
        hashed_user_ids=(
            "h:sha256:us_young_sporty_001",
            "h:sha256:us_young_sporty_002",
            "h:sha256:us_young_sporty_003",
        ),
    ),
    MockCdpSegment(
        segment_id="cdp:us_midage_fashion",
        name="US 25-34 时尚消费者",
        size_estimate=680_000,
        attributes={"geo": "US", "age": "25-34", "interest": "fashion", "device": "mobile"},
        hashed_user_ids=(
            "h:sha256:us_midage_fashion_001",
            "h:sha256:us_midage_fashion_002",
        ),
    ),
    MockCdpSegment(
        segment_id="cdp:jp_gamers_premium",
        name="JP 高价值游戏玩家",
        size_estimate=95_000,
        attributes={"geo": "JP", "age": "25-34", "interest": "gaming", "tier": "premium"},
        hashed_user_ids=(
            "h:sha256:jp_gamers_premium_001",
            "h:sha256:jp_gamers_premium_002",
            "h:sha256:jp_gamers_premium_003",
            "h:sha256:jp_gamers_premium_004",
        ),
    ),
    MockCdpSegment(
        segment_id="cdp:de_parents_family",
        name="DE 亲子家庭",
        size_estimate=310_000,
        attributes={"geo": "DE", "age": "35-44", "interest": "family", "device": "desktop"},
        hashed_user_ids=(
            "h:sha256:de_parents_family_001",
            "h:sha256:de_parents_family_002",
        ),
    ),
    MockCdpSegment(
        segment_id="cdp:global_loyal_highspend",
        name="高复购高客单价会员",
        size_estimate=58_000,
        attributes={"tier": "loyal", "interest": "lifestyle"},
        hashed_user_ids=(
            "h:sha256:global_loyal_001",
            "h:sha256:global_loyal_002",
        ),
    ),
)


def list_mock_segments() -> tuple[MockCdpSegment, ...]:
    """暴露给测试用的只读入口。生产代码不应直接读 `_MOCK_SEGMENTS`。"""
    return _MOCK_SEGMENTS


def _matches(segment: MockCdpSegment, filters: dict[str, str]) -> bool:
    return all(segment.attributes.get(key) == value for key, value in filters.items())


def query_segments(filters: dict[str, str] | None = None) -> list[dict[str, object]]:
    """按属性过滤返回候选分群。供工具包装层与测试直接调用。

    `filters` 为空 dict / None 时返回全量。返回的是序列化后的 dict(非 dataclass),
    便于工具层直接 JSON 编码给 LLM。
    """
    active_filters = filters or {}
    matched = [s for s in _MOCK_SEGMENTS if _matches(s, active_filters)]
    return [
        {
            "segment_id": s.segment_id,
            "name": s.name,
            "size_estimate": s.size_estimate,
            "attributes": dict(s.attributes),
            "hashed_user_ids": list(s.hashed_user_ids),
        }
        for s in matched
    ]


@function_tool(
    name_override="cdp_query_segments",
    description_override=(
        "查询 CDP 中满足过滤条件的候选分群。每个过滤维度独立,未提供(空字符串)表示"
        "不过滤。返回列表包含 segment_id、name、size_estimate、attributes、"
        "hashed_user_ids(已脱敏)。"
    ),
)
def cdp_query_segments_tool(
    geo: str = "",
    age: str = "",
    interest: str = "",
    device: str = "",
    tier: str = "",
) -> list[dict[str, object]]:
    """Agent 端调用的 function tool 包装。

    参数拆平(而非 dict)是为了配合 Agents SDK 的 strict JSON schema —— strict 模式下
    object 类型不允许 additionalProperties,dict[str, str] 会被拒绝。保留 `query_segments`
    供测试与 Orchestrator 内部代码使用,那里不受 strict schema 限制。
    """
    filters: dict[str, str] = {}
    for key, value in (
        ("geo", geo),
        ("age", age),
        ("interest", interest),
        ("device", device),
        ("tier", tier),
    ):
        if value:
            filters[key] = value
    return query_segments(filters)


def build_cdp_tools() -> list[FunctionTool]:
    """返回 Audience agent 要挂载的全部 CDP 工具。

    留出列表是为后续加 `cdp_lookalike_expand` 等工具预留扩展点。
    """
    return [cdp_query_segments_tool]
