"""Tracing 集成。

对应任务 `P0-004`。OpenAI Agents SDK 自带 tracing(默认把 trace 上送至
OpenAI Traces dashboard),本模块做的只是**约定封装**:

- 用 `campaign_trace()` 包住一次 campaign 相关的 agent 调用,把 `campaign_id` 固定为
  SDK 的 `group_id`,这样 Traces dashboard 上可按 campaign 聚合。
- 统一 `workflow_name` 命名空间,例:`auto_marketing_agent/<campaign>/<phase>`。
- 提供 `configure_tracing()`,支持通过环境变量 `AMA_TRACING_DISABLED=1` 在 CI / 离线
  测试下关闭上送(此时仍会产出 trace 对象,只是不落盘)。

业务代码示例(P0-032 Orchestrator 会这样用):

```python
with campaign_trace(campaign_id="camp-1", phase="plan"):
    await Runner.run(orchestrator_agent, prompt)
```
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from agents import Trace, set_tracing_disabled, trace

_DISABLE_ENV = "AMA_TRACING_DISABLED"
_WORKFLOW_PREFIX = "auto_marketing_agent"


def configure_tracing(*, disabled: bool | None = None) -> None:
    """按环境变量或显式开关配置 tracing 上送。

    - `disabled=True` → 始终关闭
    - `disabled=False` → 始终开启
    - `disabled=None` → 读 `AMA_TRACING_DISABLED` 环境变量(`1` / `true` / `yes` 表示关闭)

    调用时机:进程启动时(CLI 入口)调用一次即可。
    """
    if disabled is None:
        disabled = os.environ.get(_DISABLE_ENV, "").lower() in {"1", "true", "yes"}
    set_tracing_disabled(disabled)


def _workflow_name(phase: str) -> str:
    return f"{_WORKFLOW_PREFIX}/{phase}"


@contextmanager
def campaign_trace(
    *,
    campaign_id: str,
    phase: str,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Trace]:
    """为一段 campaign 相关的 agent 调用开启 trace。

    - `campaign_id` 作为 SDK `group_id`,方便 dashboard 按 campaign 聚合。
    - `phase` 标识这次调用在生命周期中的位置,例:`plan`、`audience`、`creative`、
      `attribution`。组合后的 `workflow_name` = `auto_marketing_agent/<phase>`。
    - `metadata` 可附带 agent 名 / model / correlation_id 等,尽量使用 `SchemaEnvelope`
      的字段,保持一致。
    """
    full_meta: dict[str, Any] = {"campaign_id": campaign_id}
    if metadata:
        full_meta.update(metadata)

    with trace(
        workflow_name=_workflow_name(phase),
        group_id=campaign_id,
        metadata=full_meta,
    ) as tr:
        yield tr
