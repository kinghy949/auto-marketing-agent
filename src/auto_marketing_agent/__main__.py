"""CLI 入口。

P0 阶段只暴露 `hello` 子命令验证 SDK 链路。P0-033 会扩展出真正的 `run --kpi ... --budget ...`
命令,进入 Orchestrator → Audience → Creative 的 handoff 流程。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from auto_marketing_agent.agents.hello import run_hello
from auto_marketing_agent.settings import load_settings
from auto_marketing_agent.tracing import configure_tracing


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-marketing-agent",
        description="自主营销活动编排系统 CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    hello = sub.add_parser("hello", help="运行 hello-world 烟雾测试 agent")
    hello.add_argument(
        "--prompt",
        default="请用一句话自我介绍,并确认你已就绪。",
        help="发给 hello agent 的提示词",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_tracing()

    if args.command == "hello":
        settings = load_settings()
        output = asyncio.run(run_hello(args.prompt, model=settings.openai_model))
        print(output)
        return 0

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
