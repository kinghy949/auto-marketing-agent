"""Mock Asset Library 工具。

P0 阶段 Creative agent 不真正调 DALL·E 或视频生成服务。`asset_library_lookup` 返回预
置的图 / 视频 URL 及授权信息,供 Creative agent 拼 `CreativeAsset`。真实 Asset Library
接入(P1)替换此模块即可,工具签名不变。

关键约束:
- 返回的 `license_id` 对应架构 §3.8 Asset Library 主键,Guardrail agent 会据此校验授权是否过期。
- 只返回有效授权(expires_at 未到)。想测过期授权路径,直接调 `lookup()` 内部函数,
  绕过 agent 层。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from agents import FunctionTool, function_tool

AssetKind = Literal["image", "video"]


@dataclass(frozen=True, slots=True)
class MockAsset:
    asset_id: str
    kind: AssetKind
    url: str
    theme: str
    language: str
    licensor: str
    license_id: str
    expires_at: datetime | None


_MOCK_ASSETS: tuple[MockAsset, ...] = (
    MockAsset(
        asset_id="asset:sport_hero_001",
        kind="image",
        url="https://assets.example.com/sport_hero_001.jpg",
        theme="sports",
        language="en-US",
        licensor="internal",
        license_id="lic-internal-2026-001",
        expires_at=None,
    ),
    MockAsset(
        asset_id="asset:sport_hero_002",
        kind="video",
        url="https://assets.example.com/sport_hero_002.mp4",
        theme="sports",
        language="en-US",
        licensor="internal",
        license_id="lic-internal-2026-002",
        expires_at=datetime(2027, 12, 31, tzinfo=timezone.utc),
    ),
    MockAsset(
        asset_id="asset:fashion_lifestyle_001",
        kind="image",
        url="https://assets.example.com/fashion_lifestyle_001.jpg",
        theme="fashion",
        language="en-US",
        licensor="getty_images",
        license_id="lic-getty-87124",
        expires_at=datetime(2028, 6, 30, tzinfo=timezone.utc),
    ),
    MockAsset(
        asset_id="asset:gaming_hero_jp_001",
        kind="image",
        url="https://assets.example.com/gaming_hero_jp_001.jpg",
        theme="gaming",
        language="ja-JP",
        licensor="internal",
        license_id="lic-internal-2026-011",
        expires_at=None,
    ),
    MockAsset(
        asset_id="asset:family_warm_de_001",
        kind="image",
        url="https://assets.example.com/family_warm_de_001.jpg",
        theme="family",
        language="de-DE",
        licensor="internal",
        license_id="lic-internal-2026-020",
        expires_at=None,
    ),
)


def list_mock_assets() -> tuple[MockAsset, ...]:
    return _MOCK_ASSETS


def _is_valid_now(asset: MockAsset) -> bool:
    if asset.expires_at is None:
        return True
    return asset.expires_at > datetime.now(timezone.utc)


def lookup(theme: str = "", language: str = "", kind: str = "") -> list[dict[str, object]]:
    """按 theme / language / kind 过滤 Asset Library。只返回授权仍有效的素材。"""
    out: list[dict[str, object]] = []
    for asset in _MOCK_ASSETS:
        if theme and asset.theme != theme:
            continue
        if language and asset.language != language:
            continue
        if kind and asset.kind != kind:
            continue
        if not _is_valid_now(asset):
            continue
        out.append(
            {
                "asset_id": asset.asset_id,
                "kind": asset.kind,
                "url": asset.url,
                "theme": asset.theme,
                "language": asset.language,
                "licensor": asset.licensor,
                "license_id": asset.license_id,
                "expires_at": asset.expires_at.isoformat() if asset.expires_at else None,
            }
        )
    return out


@function_tool(
    name_override="asset_library_lookup",
    description_override=(
        "查询 Asset Library 中可用的图 / 视频素材。theme 支持 sports / fashion / gaming / "
        "family / lifestyle;language 为 BCP-47;kind 为 image / video。空字符串表示不过滤。"
        "返回 asset_id / url / license_id / licensor / expires_at,供拼 CreativeAsset 使用。"
    ),
)
def asset_library_lookup_tool(
    theme: str = "",
    language: str = "",
    kind: str = "",
) -> list[dict[str, object]]:
    """function_tool 包装。逻辑全部委托给 `lookup`,方便测试直接调内部函数。"""
    return lookup(theme=theme, language=language, kind=kind)


def build_creative_tools() -> list[FunctionTool]:
    """返回 Creative agent 要挂载的工具集合。"""
    return [asset_library_lookup_tool]
