"""AudienceSegment v1 —— Audience agent 输出的受众分群。

架构 §7 明确要求 PII 最小化:Audience agent 只接触 **hashed** user IDs,不接触原始
PII。因此 schema 里只允许 `hashed_user_ids`,没有姓名、手机、邮箱字段。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from auto_marketing_agent.schemas.base import SchemaEnvelope


class AudienceSegment(SchemaEnvelope):
    schema_name: Literal["audience_segment"] = "audience_segment"
    schema_version: Literal["v1"] = "v1"

    segment_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, description="供运营识别的名称")
    description: str = Field(default="", description="分群构造逻辑的自然语言说明,便于审计")
    size_estimate: int = Field(..., ge=0, description="预估触达人数")
    hashed_user_ids: list[str] = Field(
        default_factory=list,
        description="SHA-256 哈希后的用户 ID 列表。原始 PII 严禁出现在此字段。",
    )
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description="维度过滤器,例:{'age': '18-24', 'geo': 'US'}",
    )
    source: str = Field(
        ...,
        min_length=1,
        description="分群来源,例:`cdp:segment`、`lookalike:meta`、`knowledge_store:recall`",
    )
    lookalike_seed_segment_id: str | None = Field(
        default=None,
        description="若此分群来自 Lookalike 扩展,记录种子 segment_id 以便溯源",
    )
