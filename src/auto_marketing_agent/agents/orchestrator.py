"""Orchestrator Agent —— 活动计划解析与编排入口。

职责(架构 §3.1):
- 读人类运营输入的自然语言 brief(KPI、预算、时间、平台、品类、红线)
- 产出 `CampaignPlan`,作为下游 agent 的"真相源"
- P0 阶段不做事件驱动 handoff;真正的 Attribution → Creative 回流 handoff 在 P2 引入。

本 agent 只负责结构化解析,不直接调用 Audience / Creative。那部分串联由
`auto_marketing_agent.agents.coordinator.run_campaign` 用 Python 编排,便于在
CI 里做确定性单测。
"""

from __future__ import annotations

from agents import Agent
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan

ORCHESTRATOR_AGENT_INSTRUCTIONS = """你是 auto-marketing-agent 的 Orchestrator 子 agent,负责把人类运营的自然语言 brief 解析成结构化 CampaignPlan。

解析规则:
- campaign_id:如用户未指定,用 `cmp:<短语料>:<YYYYMM>` 生成,slug 取品类英文。
- name:保留原文(中文),去掉标点收尾。
- primary_kpi:metric 从 roas / cac / ctr / cvr 中选一;target 为数值;comparison 用 gte(ROAS/CTR/CVR 要至少达到)或 lte(CAC 要不超过)。
- daily_budget / total_budget:按用户给的币种填 Money;total 不得低于 daily。
- start_date / end_date:ISO 日期;end_date 不得早于 start_date。
- platforms:至少 1 项,取值自 meta / google / tiktok。
- brand_guardrails:把 brief 里提到的禁用词 / 品类拆成字符串数组。
- notes:原文 brief 中无法结构化的提示。
- correlation_id:照抄入参提供的值。

歧义处理:
- 缺 KPI → 回填 metric=roas target=3.0 comparison=gte。
- 缺 total_budget → 用 daily_budget * 30。
- 缺 end_date → start_date + 30 天。
- 缺 start_date → 今天。
- 若用户明确要求不要自动回填,直接报错。
"""


def build_orchestrator_agent(model: str) -> Agent[None]:
    return Agent[None](
        name="orchestrator-agent",
        instructions=ORCHESTRATOR_AGENT_INSTRUCTIONS,
        model=model,
        output_type=CampaignPlan,
    )
