"""Creative agent 构造层测试。"""

from __future__ import annotations

from auto_marketing_agent.agents.creative import (
    CREATIVE_AGENT_INSTRUCTIONS,
    build_creative_agent,
)
from auto_marketing_agent.schemas.v1.creative import CreativeVariant


def test_build_creative_agent_basic_wiring() -> None:
    agent = build_creative_agent(model="gpt-4.1-mini")
    assert agent.name == "creative-agent"
    assert agent.model == "gpt-4.1-mini"
    assert agent.instructions == CREATIVE_AGENT_INSTRUCTIONS


def test_creative_agent_outputs_creative_variant_schema() -> None:
    agent = build_creative_agent(model="gpt-4.1-mini")
    output = agent.output_type
    resolved = getattr(output, "_output_type", output)
    assert resolved is CreativeVariant


def test_creative_agent_has_asset_lookup_tool() -> None:
    agent = build_creative_agent(model="gpt-4.1-mini")
    assert "asset_library_lookup" in {t.name for t in agent.tools}


def test_instructions_ban_inventing_license_id() -> None:
    # 避免未来改 instructions 的人把这条红线去掉
    assert "license_id" in CREATIVE_AGENT_INSTRUCTIONS
    assert "禁止自己编" in CREATIVE_AGENT_INSTRUCTIONS
