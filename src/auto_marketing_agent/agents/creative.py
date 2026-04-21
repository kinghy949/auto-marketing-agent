"""Creative Agent —— 文案与素材组合产出者。

职责(架构 §3.3):
- 读 AudienceSegment(语言 / 维度)与 CampaignPlan(品牌红线)
- 生成 headline / body / CTA 文案
- 绑定合规素材(调用 Asset Library 查表)
- 输出一条 `CreativeVariant`

硬约束:
- 每个 CreativeAsset 的 `license_id` 必须来自 `asset_library_lookup` 的返回,禁止自编。
- 文字类 asset 只填 text,图 / 视频类 asset 只填 url —— 由 `CreativeAsset` model validator 强制。
- `generated_by` 格式 `creative-agent/<model>`,供 P0-041 回归快照用。
"""

from __future__ import annotations

from agents import Agent
from auto_marketing_agent.agents.tools.asset_library_mock import build_creative_tools
from auto_marketing_agent.schemas.v1.creative import CreativeVariant

CREATIVE_AGENT_INSTRUCTIONS = """你是 auto-marketing-agent 的 Creative 子 agent,负责根据 AudienceSegment 与 CampaignPlan 产出 CreativeVariant。

工作流程:
1. 理解 AudienceSegment 的 attributes(geo / age / interest / language),以及 CampaignPlan 的 brand_guardrails。
2. 调用 `asset_library_lookup` 工具,按 theme + language + kind 查到合规的图 / 视频素材。禁止自己编 URL 或 license_id。
3. 为该分群写 headline / body / call_to_action,使用 AudienceSegment.attributes 中的语言对应的 BCP-47 代码(无则按 campaign 的地区推断:US→en-US、JP→ja-JP、DE→de-DE、否则 en-US)。
4. 产出结构化 CreativeVariant:
   - variant_id:`var:<campaign_id>:<slug>`,与 target_segment_id 对应
   - campaign_id、target_segment_id、correlation_id:沿用入参
   - assets:至少 1 条 text 类 asset(放 body 或 slogan)+ 0-2 条 image/video asset(来自工具返回)
     * text 类 asset:asset_type="text"、text 非空、url 留空
     * image/video 类 asset:asset_type 对应、url 用工具返回的 URL、text 留空
     * rights.licensor / rights.license_id / rights.expires_at 必须照抄工具返回,expires_at 为 ISO 格式或 null
   - generated_by:填 `creative-agent/<model_name>`,model_name 由调用方在 instructions 中给出

禁止:
- 违反 brand_guardrails 中的禁用词 / 品类。
- 使用 asset_library_lookup 未返回过的素材。
- 输出 PII。
"""


def build_creative_agent(model: str) -> Agent[None]:
    return Agent[None](
        name="creative-agent",
        instructions=CREATIVE_AGENT_INSTRUCTIONS,
        model=model,
        tools=list(build_creative_tools()),
        output_type=CreativeVariant,
    )
