"""CreativeVariant v1 —— Creative agent 输出的单条广告素材。

每条 variant 可能绑定多个 asset(文案 + 图 / 视频)。`AssetRights` 字段对应架构 §3.8
Asset Library 的授权追踪:无授权或过期资产必须被 Guardrail 拒绝。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_marketing_agent.schemas.base import SchemaEnvelope

AssetType = Literal["image", "video", "text"]


class AssetRights(BaseModel):
    """资产授权信息。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    licensor: str = Field(..., min_length=1, description="授权方,例:`internal`、`getty_images`")
    license_id: str = Field(..., min_length=1, description="授权凭证编号,Asset Library 主键")
    expires_at: datetime | None = Field(
        default=None,
        description="授权到期时间。None 表示永久。Guardrail 每次使用前必须校验。",
    )


class CreativeAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(..., min_length=1, description="Asset Library 中的唯一 ID")
    asset_type: AssetType
    url: str | None = Field(
        default=None,
        description="图 / 视频的可访问 URL。文案类 asset 为空。",
    )
    text: str | None = Field(
        default=None,
        description="文案内容。图 / 视频类 asset 为空。",
    )
    rights: AssetRights

    @model_validator(mode="after")
    def _check_payload_shape(self) -> CreativeAsset:
        if self.asset_type == "text":
            if not self.text:
                raise ValueError("text 类 asset 必须携带 `text` 字段")
            if self.url is not None:
                raise ValueError("text 类 asset 不应携带 `url`")
        else:  # image / video
            if not self.url:
                raise ValueError(f"{self.asset_type} 类 asset 必须携带 `url`")
            if self.text is not None:
                raise ValueError(f"{self.asset_type} 类 asset 不应携带 `text`")
        return self


class CreativeVariant(SchemaEnvelope):
    schema_name: Literal["creative_variant"] = "creative_variant"
    schema_version: Literal["v1"] = "v1"

    variant_id: str = Field(..., min_length=1)
    campaign_id: str = Field(..., min_length=1)
    target_segment_id: str = Field(
        ...,
        min_length=1,
        description="此 variant 面向的 AudienceSegment.segment_id",
    )
    headline: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    call_to_action: str = Field(..., min_length=1)
    language: str = Field(
        ...,
        pattern=r"^[a-z]{2}(-[A-Z]{2})?$",
        description="BCP-47 语言代码,例:`zh`、`zh-CN`、`en-US`",
    )
    assets: list[CreativeAsset] = Field(default_factory=list)
    generated_by: str = Field(
        ...,
        min_length=1,
        description="生成者,格式 `agent_name/model_name`,供 eval 与回归追踪",
    )
