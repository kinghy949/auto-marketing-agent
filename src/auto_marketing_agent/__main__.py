"""CLI 入口。

- `hello` 子命令:验证 SDK 链路是否就绪。
- `run` 子命令:按 brief 跑一次 Orchestrator → Audience → Creative,把三份结构化
  payload 以 JSON 打到 stdout,供人工抽检或落盘。

`run` 设计为"一次性、非交互"命令 —— 要交互可选 session 要加回跑计划要 resume,
等 P1 引入 Session / Event Store 再补。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from auto_marketing_agent.agents.coordinator import (
    CampaignRunResult,
    build_default_agents,
    run_campaign,
)
from auto_marketing_agent.agents.hello import run_hello
from auto_marketing_agent.hitl import InMemoryHitlQueue
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

    run = sub.add_parser(
        "run",
        help="按 brief 跑一次 Orchestrator → Audience → Creative,输出 JSON",
    )
    run.add_argument(
        "--brief",
        required=True,
        help="自然语言活动 brief(KPI、预算、时间、平台、红线等)",
    )
    run.add_argument(
        "--correlation-id",
        default=None,
        help="链路追踪 ID。不传则自动生成 `corr:<uuid4>`。",
    )
    run.add_argument(
        "--model",
        default=None,
        help="覆盖默认模型名。不传则用 Settings.openai_model。",
    )
    return parser


def _dump_result(result: CampaignRunResult) -> str:
    payload: dict[str, object] = {
        "plan": json.loads(result.plan.model_dump_json()),
        "segment": json.loads(result.segment.model_dump_json()),
        "variant": json.loads(result.variant.model_dump_json()),
        "approval": json.loads(result.approval.model_dump_json()),
    }
    if result.hitl_item is not None:
        payload["hitl_item_id"] = result.hitl_item.item_id
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    configure_tracing()

    if args.command == "hello":
        settings = load_settings()
        output = asyncio.run(run_hello(args.prompt, model=settings.openai_model))
        print(output)
        return 0

    if args.command == "run":
        settings = load_settings()
        model = args.model or settings.openai_model
        correlation_id = args.correlation_id or f"corr:{uuid.uuid4()}"
        agents = build_default_agents(model=model)
        hitl_queue = InMemoryHitlQueue()
        result = asyncio.run(
            run_campaign(
                brief=args.brief,
                correlation_id=correlation_id,
                agents=agents,
                hitl_queue=hitl_queue,
            )
        )
        print(_dump_result(result))
        return 0

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
