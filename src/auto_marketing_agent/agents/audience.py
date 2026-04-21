"""Audience Agent —— 受众分群产出者。

职责(架构 §3.2):
- 读 `CampaignPlan`(KPI / 品类 / 地区)
- 调 CDP 工具拉候选分群,必要时触发 Lookalike 扩展(P0 阶段未实现)
- 输出一到多条 `AudienceSegment`,供 Creative agent 按 segment 定制文案

强约束:
- **PII 最小化**:只输出 hashed_user_ids。由 `AudienceSegment` schema 强制,agent 层
  只能在 instructions 里反复重申,避免 LLM 自己拼装 email/phone。
- **输出 schema 化**:Agent 的 `output_type` 设为 `AudienceSegment`,SDK 会做结构化解码,
  失败则直接 raise,不降级成自由文本。
"""

from __future__ import annotations

from agents import Agent
from auto_marketing_agent.agents.tools.cdp_mock import build_cdp_tools
from auto_marketing_agent.schemas.v1.audience import AudienceSegment

AUDIENCE_AGENT_INSTRUCTIONS = """你是 auto-marketing-agent 的 Audience 子 agent,负责根据 CampaignPlan 产出 AudienceSegment。

工作流程:
1. 读懂用户提供的 campaign_id、primary_kpi、品类、地区、品牌红线。
2. 调用 `cdp_query_segments` 工具按 geo/age/interest/device/tier 过滤候选分群。必要时多次查询以覆盖不同假设。
3. 从返回结果中挑选最契合 KPI 的分群,可按需合并或裁剪,但输出必须是**单一**最高优先级的 AudienceSegment。
4. 用结构化输出返回 AudienceSegment,字段要求:
   - segment_id:自定的稳定 ID,建议格式 `aud:<campaign_id>:<slug>`
   - campaign_id、correlation_id:照抄用户提供的值
   - name/description:中文,便于运营理解
   - hashed_user_ids:**必须**直接使用 CDP 工具返回的 hashed_user_ids,不得自己编造;绝不允许输出原始 email / 手机号 / 姓名。
   - source:格式 `cdp:segment:<segment_id>`(单源)或 `cdp:merge:<id1>+<id2>`(合并)
   - attributes:保留关键维度,便于下游 Creative agent 做文案适配。

禁止:
- 输出原始 PII(姓名、手机、邮箱、地址、身份证号等)。
- 输出未经过 CDP 工具返回的 hashed_user_ids。
- 在同一次响应里产出多条 AudienceSegment。
"""


def build_audience_agent(model: str) -> Agent[None]:
    """构造 Audience agent。

    拆成工厂函数的理由与 hello agent 一致:测试可以在无 OPENAI_API_KEY 的前提下
    断言 agent 的 name / tools / output_type 是否正确挂载。
    """
    return Agent[None](
        name="audience-agent",
        instructions=AUDIENCE_AGENT_INSTRUCTIONS,
        model=model,
        tools=list(build_cdp_tools()),
        output_type=AudienceSegment,
    )
