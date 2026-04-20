"""hello agent 构造层的单元测试。

这里只验证 `build_hello_agent` 能正确产出 Agent 实例,不实际调用 OpenAI API —— 真实调用
留到 golden case / 集成测试(P0-040+)。
"""

from __future__ import annotations

from auto_marketing_agent.agents.hello import HELLO_AGENT_INSTRUCTIONS, build_hello_agent


def test_build_hello_agent_uses_given_model() -> None:
    agent = build_hello_agent(model="gpt-4.1-mini")

    assert agent.name == "hello-probe"
    assert agent.model == "gpt-4.1-mini"
    assert agent.instructions == HELLO_AGENT_INSTRUCTIONS
