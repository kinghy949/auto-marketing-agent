"""CLI 入口。

- `hello` 子命令:验证 SDK 链路是否就绪。
- `run` 子命令:按 brief 跑一次 Orchestrator → Audience → Creative,把三份结构化
  payload 以 JSON 打到 stdout,供人工抽检或落盘。传 `--events-file` 把事件按
  append-only JSONL 持久化,下次 `events replay` 就能重放。
- `events replay` 子命令:从 JSONL 文件加载事件,按 campaign / correlation /
  time-range 过滤后按 occurred_at 升序 dump,供重放、审计、离线 eval 使用。

`run` 设计为"一次性、非交互"命令 —— 要交互可选 session 要加回跑计划要 resume,
等 P1 引入 Session / Event Store 再补。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import cast

from auto_marketing_agent.agents.coordinator import (
    CampaignRunResult,
    build_default_agents,
    run_campaign,
)
from auto_marketing_agent.agents.hello import run_hello
from auto_marketing_agent.events import (
    Event,
    EventStore,
    EventType,
    InMemoryEventStore,
    JsonlEventStore,
)
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
    run.add_argument(
        "--events-file",
        default=None,
        help="事件落盘路径(JSONL,append)。不传则只在内存里累积,进程退出后丢弃。",
    )

    events = sub.add_parser(
        "events",
        help="Event Store 运维命令(重放 / 查询)",
    )
    events_sub = events.add_subparsers(dest="events_command", required=True)
    replay = events_sub.add_parser(
        "replay",
        help="从 JSONL 文件按 occurred_at 升序回放事件",
    )
    replay.add_argument(
        "--events-file",
        required=True,
        help="JSONL 事件文件路径(由 `run --events-file` 产出)",
    )
    replay.add_argument(
        "--campaign-id",
        default=None,
        help="仅回放该 campaign 的事件",
    )
    replay.add_argument(
        "--correlation-id",
        default=None,
        help="仅回放该 correlation 的事件",
    )
    replay.add_argument(
        "--event-type",
        default=None,
        help="仅回放该类型的事件(如 cost_guard.denied)",
    )
    replay.add_argument(
        "--since",
        default=None,
        help="ISO-8601 起始时间(tz-aware,如 2026-04-22T00:00:00+00:00)",
    )
    replay.add_argument(
        "--until",
        default=None,
        help="ISO-8601 截止时间(tz-aware,半开区间上界)",
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


def _event_to_jsonable(event: Event) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "source": event.source,
        "schema_version": event.schema_version,
        "correlation_id": event.correlation_id,
        "campaign_id": event.campaign_id,
        "payload": event.payload,
        "occurred_at": event.occurred_at.isoformat(),
    }


def _run_replay(args: argparse.Namespace) -> int:
    """从 JSONL 文件读取事件,按过滤条件组合查询后按 occurred_at 升序打印。"""
    path = Path(args.events_file)
    if not path.exists():
        print(f"events file 不存在: {path}", file=sys.stderr)
        return 2
    store = JsonlEventStore(path=path)

    # 任一过滤都带 time_range / type 就走 list_by_time_range,拿到之后再做
    # campaign / correlation 过滤。单表 O(N) 扫描,P1 规模没必要组合索引。
    start = _parse_iso(args.since, field="--since") if args.since else None
    end = _parse_iso(args.until, field="--until") if args.until else None
    event_type = cast(EventType, args.event_type) if args.event_type else None

    events = store.list_by_time_range(start=start, end=end, event_type=event_type)
    if args.campaign_id:
        events = [e for e in events if e.campaign_id == args.campaign_id]
    if args.correlation_id:
        events = [e for e in events if e.correlation_id == args.correlation_id]

    for e in events:
        print(json.dumps(_event_to_jsonable(e), ensure_ascii=False))
    return 0


def _parse_iso(value: str, *, field: str) -> datetime:
    """把 CLI 传入的 ISO-8601 字符串解析成 tz-aware datetime。

    拒掉 naive —— 重放时间语义必须明确,否则跨时区对比会出错。
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise SystemExit(f"{field} 解析失败({value!r}):{e}") from e
    if parsed.tzinfo is None:
        raise SystemExit(f"{field} 必须是 tz-aware(如 ...+00:00),收到 {value!r}")
    return parsed


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
        # 有 --events-file 就落盘 JSONL,便于 `events replay` 消费;否则只在
        # 内存里累积,进程退出即丢。
        event_store: EventStore
        if args.events_file:
            event_store = JsonlEventStore(path=Path(args.events_file))
        else:
            event_store = InMemoryEventStore()
        result = asyncio.run(
            run_campaign(
                brief=args.brief,
                correlation_id=correlation_id,
                agents=agents,
                hitl_queue=hitl_queue,
                event_store=event_store,
            )
        )
        output = _dump_result(result)
        output_obj = json.loads(output)
        output_obj["event_count"] = len(event_store)
        print(json.dumps(output_obj, ensure_ascii=False, indent=2))
        return 0

    if args.command == "events":
        if args.events_command == "replay":
            return _run_replay(args)
        parser.error(f"未知 events 子命令: {args.events_command}")
        return 2

    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
