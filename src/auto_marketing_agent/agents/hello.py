"""Hello-world agent,用于验证 `openai-agents` SDK 是否跑通。

此 agent 不承担任何业务职责,仅作为骨架期烟雾测试:
- 能加载配置
- 能构造 Agent
- 能执行一次 Runner 调用并返回文本

业务 agent(Audience / Creative / Orchestrator 等)在 P0-030+ 阶段才开始落地。
"""

from __future__ import annotations

from agents import Agent, Runner

HELLO_AGENT_INSTRUCTIONS = (
    "你是 auto-marketing-agent 项目的 hello-world 探针。"
    "收到任何输入时,用一句中文回应,确认 SDK、模型调用链路与 tracing 正常工作。"
)


def build_hello_agent(model: str) -> Agent:
    """构造 hello agent。

    拆成独立函数是为了让测试可以在不依赖环境变量的前提下验证构造逻辑。
    """
    return Agent(
        name="hello-probe",
        instructions=HELLO_AGENT_INSTRUCTIONS,
        model=model,
    )


async def run_hello(prompt: str, model: str) -> str:
    """执行一次 hello agent 调用,返回最终文本输出。"""
    agent = build_hello_agent(model=model)
    result = await Runner.run(agent, prompt)
    return str(result.final_output)
