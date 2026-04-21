"""Schema Registry 查表与版本化。

所有受管 payload 通过 `(schema_name, schema_version)` 在此注册。未来反序列化未知
JSON 时(P1-032 DLQ 流程)用 `resolve()` 拿到模型类做校验,失败即进 DLQ。

### 新增版本的工作流

1. 在 `schemas/v<N>/` 下新增 / 修改模型(v1 模型不可原地破坏性修改)。
2. 在本文件的 `_REGISTRY` 字典里补一条 `(name, "v<N>")` 映射。
3. 在 `tests/schemas/test_registry.py` 补契约测试,保证 `all_schemas()` 列表完整。
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from auto_marketing_agent.schemas.v1 import (
    ApprovalDecision,
    AttributionReport,
    AudienceSegment,
    BuyOrder,
    CampaignPlan,
    CreativeVariant,
    ExperimentSpec,
)

SchemaKey = tuple[str, str]
"""(schema_name, schema_version) 元组。"""

SchemaCls = type[BaseModel]

_REGISTRY: dict[SchemaKey, SchemaCls] = {
    ("campaign_plan", "v1"): CampaignPlan,
    ("audience_segment", "v1"): AudienceSegment,
    ("creative_variant", "v1"): CreativeVariant,
    ("buy_order", "v1"): BuyOrder,
    ("experiment_spec", "v1"): ExperimentSpec,
    ("attribution_report", "v1"): AttributionReport,
    ("approval_decision", "v1"): ApprovalDecision,
}


class UnknownSchemaError(LookupError):
    """Registry 未注册的 (schema_name, schema_version) 组合。"""


def resolve(name: str, version: str) -> SchemaCls:
    """按 (name, version) 返回 Pydantic 模型类;未命中抛 `UnknownSchemaError`。"""
    try:
        return _REGISTRY[(name, version)]
    except KeyError as exc:
        raise UnknownSchemaError(f"未注册的 schema: {name}@{version}") from exc


def all_schemas() -> Iterator[tuple[SchemaKey, SchemaCls]]:
    """迭代所有 (key, cls),供 CI 契约测试与文档生成使用。"""
    yield from _REGISTRY.items()
