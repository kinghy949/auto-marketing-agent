"""Golden case 回归测试(P0-042)。

对 `tests/golden/{audience,creative}/*.json` 下的每份样本:

1. 能被对应 schema 完整解析(schema drift 早报警)。
2. 字段不含任何 extra(SchemaEnvelope extra="forbid" 的回归)。
3. 领域红线断言:
   - Audience:所有 hashed_user_ids 以 `h:` 开头;attributes 里不出现原始 PII 键。
   - Creative:每条 asset 都带非空 license_id / licensor;text/url 与 asset_type 匹配。
4. 交叉一致性:同一文件内 correlation_id / campaign_id / id 命名与文件前缀对齐。

这组测试是 CI 阻塞的 —— 它回答"我们的 schema 是否能吐出预期的 payload",在 P2 eval
pipeline 上线前就守住最基础的回归底线。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_marketing_agent.schemas.v1.audience import AudienceSegment
from auto_marketing_agent.schemas.v1.creative import CreativeVariant

_GOLDEN_ROOT = Path(__file__).parent / "golden"

_AUDIENCE_FILES = sorted((_GOLDEN_ROOT / "audience").glob("*.json"))
_CREATIVE_FILES = sorted((_GOLDEN_ROOT / "creative").glob("*.json"))

_PII_KEYS = {"email", "phone", "name", "address", "idcard", "ssn"}


def test_golden_dirs_have_minimum_case_count() -> None:
    # P0-040/041 要求每个 agent 至少 5 条;少于这个数说明有人删样本,要拒入。
    assert len(_AUDIENCE_FILES) >= 5, "Audience golden case 少于 5 条"
    assert len(_CREATIVE_FILES) >= 5, "Creative golden case 少于 5 条"


@pytest.mark.parametrize("path", _AUDIENCE_FILES, ids=lambda p: p.name)
def test_audience_golden_case_loads_and_validates(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    segment = AudienceSegment.model_validate(raw)

    for uid in segment.hashed_user_ids:
        assert uid.startswith("h:"), f"{path.name} 疑似原始 PII: {uid!r}"

    offending = set(segment.attributes.keys()) & _PII_KEYS
    assert not offending, f"{path.name} attributes 含 PII 键: {offending}"

    assert segment.source, f"{path.name} source 不能为空"


@pytest.mark.parametrize("path", _CREATIVE_FILES, ids=lambda p: p.name)
def test_creative_golden_case_loads_and_validates(path: Path) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    variant = CreativeVariant.model_validate(raw)

    assert variant.assets, f"{path.name} 至少需 1 条 asset"
    for asset in variant.assets:
        assert asset.rights.licensor.strip(), f"{path.name} asset {asset.asset_id} licensor 为空"
        assert asset.rights.license_id.strip(), (
            f"{path.name} asset {asset.asset_id} license_id 为空"
        )
        # asset_type 与 text/url 的约束由 CreativeAsset model_validator 管,这里再贴一层兜底断言
        if asset.asset_type == "text":
            assert asset.text and not asset.url
        else:
            assert asset.url and not asset.text

    assert variant.generated_by.startswith("creative-agent/"), (
        f"{path.name} generated_by 应以 creative-agent/ 开头"
    )


@pytest.mark.parametrize("path", _AUDIENCE_FILES, ids=lambda p: p.name)
def test_audience_file_name_matches_correlation(path: Path) -> None:
    # 文件名形如 `01_us_young_sporty.json`;correlation_id 形如 `corr:golden-aud-01`。
    # 取数字前缀做对齐,保证谁都不能偷偷替换 payload 但忘改文件名。
    prefix = path.stem.split("_", 1)[0]
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["correlation_id"].endswith(f"-{prefix}"), (
        f"{path.name} correlation_id {raw['correlation_id']!r} 与文件前缀 {prefix} 不对齐"
    )


@pytest.mark.parametrize("path", _CREATIVE_FILES, ids=lambda p: p.name)
def test_creative_file_name_matches_correlation(path: Path) -> None:
    prefix = path.stem.split("_", 1)[0]
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["correlation_id"].endswith(f"-{prefix}"), (
        f"{path.name} correlation_id {raw['correlation_id']!r} 与文件前缀 {prefix} 不对齐"
    )
