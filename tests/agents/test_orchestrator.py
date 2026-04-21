"""Orchestrator agent 构造层测试。"""

from __future__ import annotations

from auto_marketing_agent.agents.orchestrator import (
    ORCHESTRATOR_AGENT_INSTRUCTIONS,
    build_orchestrator_agent,
)
from auto_marketing_agent.schemas.v1.campaign import CampaignPlan


def test_build_orchestrator_agent_basic_wiring() -> None:
    agent = build_orchestrator_agent(model="gpt-4.1-mini")
    assert agent.name == "orchestrator-agent"
    assert agent.model == "gpt-4.1-mini"
    assert agent.instructions == ORCHESTRATOR_AGENT_INSTRUCTIONS


def test_orchestrator_outputs_campaign_plan_schema() -> None:
    agent = build_orchestrator_agent(model="gpt-4.1-mini")
    output = agent.output_type
    resolved = getattr(output, "_output_type", output)
    assert resolved is CampaignPlan


def test_orchestrator_has_no_tools_in_p0() -> None:
    # P0 阶段 Orchestrator 只做结构化解析,不需要挂工具。P1 才接 Knowledge Store recall。
    agent = build_orchestrator_agent(model="gpt-4.1-mini")
    assert agent.tools == []
