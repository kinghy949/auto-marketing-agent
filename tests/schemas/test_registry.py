"""Schema Registry 契约测试。

目标:保证 Registry 暴露的模型类与 `schema_name`/`schema_version` 字段一致。新增
schema 只需在 Registry 加一行,这里会自动覆盖。
"""

from __future__ import annotations

import pytest

from auto_marketing_agent.schemas import (
    SchemaEnvelope,
    UnknownSchemaError,
    all_schemas,
    resolve,
)


def test_all_schemas_non_empty() -> None:
    items = list(all_schemas())
    assert items, "Registry 不应为空"


def test_registered_classes_are_envelope_subclasses() -> None:
    for _, cls in all_schemas():
        assert issubclass(cls, SchemaEnvelope), f"{cls.__name__} 必须继承 SchemaEnvelope"


def test_registry_key_matches_class_literal_defaults() -> None:
    """注册键必须和类里 Literal 默认值对齐,避免 resolve() 拿到错的类。"""
    for (name, version), cls in all_schemas():
        name_default = cls.model_fields["schema_name"].default
        version_default = cls.model_fields["schema_version"].default
        assert name_default == name, f"{cls.__name__}.schema_name 默认值与 Registry 键不一致"
        assert version_default == version, (
            f"{cls.__name__}.schema_version 默认值与 Registry 键不一致"
        )


def test_resolve_hits() -> None:
    for key, cls in all_schemas():
        assert resolve(*key) is cls


def test_resolve_miss_raises() -> None:
    with pytest.raises(UnknownSchemaError):
        resolve("does_not_exist", "v1")
