"""Guardrail 机审工具。

把 `guardrail.engine.evaluate_variant` 包装成 Agents SDK 的 function_tool,供 Guardrail
agent 调用。**真正的决策逻辑全部在 engine 侧**,LLM 只是把变体塞进来、把结果透传出去。
这样:

- 机器规则与 LLM 裁定分离:生产环境希望违规的决策必须可复现,不能被 LLM 的措辞影响。
- coordinator 的函数式调用(`evaluate_variant`)与 agent 的工具调用共用同一套规则,
  升级规则无须改两处。

SDK 的严格 JSON schema 不允许对象自由字段,所以变体用 **扁平字符串参数** 传入,
而不是传整个 CreativeVariant JSON。这样能避免 "additionalProperties" 问题,也让 agent
instructions 更好写(字段对齐 prompt 容易理解)。
"""

from __future__ import annotations

from agents import FunctionTool, function_tool
from auto_marketing_agent.guardrail.engine import default_engine
from auto_marketing_agent.schemas.v1.creative import (
    AssetRights,
    CreativeAsset,
    CreativeVariant,
)

_DEFAULT_ENGINE = default_engine()


def _build_text_only_variant(
    *,
    correlation_id: str,
    campaign_id: str,
    variant_id: str,
    target_segment_id: str,
    headline: str,
    body: str,
    call_to_action: str,
    language: str,
    extra_text: str,
) -> CreativeVariant:
    """构造一个仅用于扫文本的 CreativeVariant。

    Guardrail 机审只看文本字段,不检查资产 URL 合法性,因此这里强行造一个 text 类
    asset 把 `extra_text`(例如 slogan)塞进 assets[0],其他 image / video 字段忽略。
    如果 `extra_text` 为空,就不塞 asset,让 variant 的 assets 列表为空 —— schema 允许。
    """
    assets: list[CreativeAsset] = []
    if extra_text.strip():
        assets.append(
            CreativeAsset(
                asset_id="asset:guardrail_check:text",
                asset_type="text",
                text=extra_text,
                rights=AssetRights(licensor="internal", license_id="lic-guardrail-stub"),
            )
        )
    return CreativeVariant(
        correlation_id=correlation_id,
        variant_id=variant_id,
        campaign_id=campaign_id,
        target_segment_id=target_segment_id,
        headline=headline,
        body=body,
        call_to_action=call_to_action,
        language=language,
        assets=assets,
        generated_by="guardrail-agent/check-stub",
    )


@function_tool(
    name_override="guardrail_evaluate_variant",
    description_override=(
        "对给定 CreativeVariant 文本字段做机审。返回 decision (approve/reject/needs_hitl)、"
        "violations (rule_id 列表)、modification_suggestions (修改建议)、rationale。"
        "brand_guardrails 是品牌禁用词 / 品类数组,来自 CampaignPlan.brand_guardrails。"
    ),
)
def guardrail_evaluate_variant_tool(
    correlation_id: str,
    campaign_id: str,
    variant_id: str,
    target_segment_id: str,
    headline: str,
    body: str,
    call_to_action: str,
    language: str,
    brand_guardrails: list[str],
    extra_text: str = "",
) -> dict[str, object]:
    """function_tool 包装。

    返回 dict 而非 ApprovalDecision 实例 —— SDK 序列化 dict 稳定,且 agent 拿到 dict
    后要再走一次 output_type 结构化,二次解码有值(避免 tool 直接返回 Pydantic 实例时
    SDK 路径的边缘问题)。
    """
    variant = _build_text_only_variant(
        correlation_id=correlation_id,
        campaign_id=campaign_id,
        variant_id=variant_id,
        target_segment_id=target_segment_id,
        headline=headline,
        body=body,
        call_to_action=call_to_action,
        language=language,
        extra_text=extra_text,
    )
    decision = _DEFAULT_ENGINE.evaluate_variant(
        variant,
        brand_guardrails=brand_guardrails,
        correlation_id=correlation_id,
    )
    return decision.model_dump(mode="json")


def build_guardrail_tools() -> list[FunctionTool]:
    return [guardrail_evaluate_variant_tool]
