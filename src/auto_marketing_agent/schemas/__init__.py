"""Schema Registry 包。

集中管理 agent 之间 handoff payload 的 Pydantic 模型与版本。对外暴露:

- `SchemaEnvelope` —— 所有 payload 的公共基类,强制 `schema_name`/`schema_version`/
  `correlation_id` 字段,用于 Registry 查表与链路追踪。
- `registry` 子模块 —— 通过 `resolve(name, version)` 拿到模型类。
- `v1` 子包 —— 当前稳定版本的模型类,按业务概念分文件。

架构约束见 `docs/architecture.md` §3.6:反序列化失败必须进 DLQ,不要降级为 dict。
"""

from auto_marketing_agent.schemas.base import SchemaEnvelope
from auto_marketing_agent.schemas.registry import (
    SchemaKey,
    UnknownSchemaError,
    all_schemas,
    resolve,
)

__all__ = [
    "SchemaEnvelope",
    "SchemaKey",
    "UnknownSchemaError",
    "all_schemas",
    "resolve",
]
