"""Audience agent 构造层测试。

只验证 agent 的装配(instructions、tools、output_type),不真实调模型 ——
end-to-end 行为由 P0-042 golden cases 覆盖,那里会在 CI 里跑 eval。
"""

from __future__ import annotations

from auto_marketing_agent.agents.audience import (
    AUDIENCE_AGENT_INSTRUCTIONS,
    build_audience_agent,
)
from auto_marketing_agent.schemas.v1.audience import AudienceSegment


def test_build_audience_agent_basic_wiring() -> None:
    agent = build_audience_agent(model="gpt-4.1-mini")
    assert agent.name == "audience-agent"
    assert agent.instructions == AUDIENCE_AGENT_INSTRUCTIONS
    assert agent.model == "gpt-4.1-mini"


def test_audience_agent_outputs_audience_segment_schema() -> None:
    agent = build_audience_agent(model="gpt-4.1-mini")
    # Agents SDK 把 output_type 包成 AgentOutputSchema,原始类保留在 output_type 属性里
    # 或 schema 的 _output_type。两种兼容写法。
    output = agent.output_type
    assert output is not None
    resolved = getattr(output, "_output_type", output)
    assert resolved is AudienceSegment


def test_audience_agent_has_cdp_query_tool() -> None:
    agent = build_audience_agent(model="gpt-4.1-mini")
    tool_names = {t.name for t in agent.tools}
    assert "cdp_query_segments" in tool_names


def test_instructions_mention_pii_minimization() -> None:
    # 人看的红线也要写在 instructions 里 —— 这是给未来改动 instructions 的人一个硬约束。
    assert "hashed_user_ids" in AUDIENCE_AGENT_INSTRUCTIONS
    assert "原始 PII" in AUDIENCE_AGENT_INSTRUCTIONS or "原始 email" in AUDIENCE_AGENT_INSTRUCTIONS
