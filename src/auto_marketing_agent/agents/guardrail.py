"""Guardrail Agent(P1-052)—— 机审 + HITL 分流。

职责(架构 §3.4):
- 接收一条 CreativeVariant 及所属 CampaignPlan 的 brand_guardrails。
- 调 `guardrail_evaluate_variant` 工具做确定性规则匹配。
- 把工具返回原样输出为 `ApprovalDecision`。
- 如命中 needs_hitl,由更上层的 HITL 子系统(P1-053)接管排队,本 agent 不自行发通知。

设计取舍:
- **决策逻辑不过 LLM**:机器规则必须可复现。LLM 只负责"填表",不负责"判罚"。
  如果以后要补 LLM 语义审查(例:暗讽、双关禁忌),再叠一层 `GuardrailLLMReviewer`,
  不要让它污染现有确定性管道。
- **不挂 handoff**:P1 阶段 coordinator 用 Python 函数调用触发机审;P2 才在 Attribution
  事件流里把 Guardrail 作为 handoff 目标。
"""

from __future__ import annotations

from agents import Agent
from auto_marketing_agent.agents.tools.guardrail_check import build_guardrail_tools
from auto_marketing_agent.schemas.v1.approval import ApprovalDecision

GUARDRAIL_AGENT_INSTRUCTIONS = """你是 auto-marketing-agent 的 Guardrail 子 agent,负责对 Creative 产出的 CreativeVariant 做机审。

工作流程:
1. 用户会给你一条 CreativeVariant JSON(含 headline / body / call_to_action / language 等)与 CampaignPlan.brand_guardrails 列表。
2. 调用 `guardrail_evaluate_variant` 工具,参数与 CreativeVariant 字段一一对应:
   - correlation_id、campaign_id、variant_id、target_segment_id 照抄
   - headline / body / call_to_action / language 照抄
   - brand_guardrails 照抄输入的数组
   - extra_text:若 variant 的 assets 中存在 text 类 asset,把它的 text 塞进 extra_text;多条则用换行拼接。无则传空串。
3. 将工具返回的 JSON **逐字段** 填到结构化输出 ApprovalDecision 中,不得改动 decision / violations / rationale / modification_suggestions 的任何内容。
4. 如工具返回 decision=reject,violations 必须非空(schema 约束)。

禁止:
- 自行改写 decision 或 violations。
- 用自然语言推断规则是否命中,必须依赖工具返回。
- 将原始 PII / 个人信息写入 rationale。
"""


def build_guardrail_agent(model: str) -> Agent[None]:
    return Agent[None](
        name="guardrail-agent",
        instructions=GUARDRAIL_AGENT_INSTRUCTIONS,
        model=model,
        tools=list(build_guardrail_tools()),
        output_type=ApprovalDecision,
    )
