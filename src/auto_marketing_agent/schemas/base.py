"""Schema 公共基类。

`SchemaEnvelope` 是所有 handoff payload 的根类,集中承担三件事:

1. 强类型 + 严格模式(`extra="forbid"`):反序列化到未知字段直接失败,契合架构 §3.6
   "反序列化失败 → DLQ" 的设计。
2. 版本标识(`schema_name` + `schema_version`):供 Registry 查表,也是 Event Store
   审计的基础。
3. 链路追踪(`correlation_id`)与审计时间戳(`created_at`):跨 agent handoff 必带。

子类通过 `Literal` 类型把 `schema_name`/`schema_version` 固化为常量,既让静态检查拒绝
写错,又能在 JSON 反序列化时做 tag 路由。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class SchemaEnvelope(BaseModel):
    """所有受 Registry 管理的 payload 的公共基类。

    不直接实例化 —— 业务 schema 在 `schemas/v<N>/` 下定义,继承此类并用 `Literal`
    约束 `schema_name` 与 `schema_version`。
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    schema_name: str = Field(
        ...,
        description="schema 逻辑名,在 Registry 中唯一。例:`audience_segment`",
    )
    schema_version: str = Field(
        ...,
        description="schema 版本号,与所在版本目录一致。例:`v1`",
    )
    correlation_id: str = Field(
        ...,
        min_length=1,
        description="跨 agent handoff 的链路追踪 ID,由 Orchestrator 生成并沿途传递",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="payload 构造时间(UTC),供 Event Store 审计与时序重放",
    )
