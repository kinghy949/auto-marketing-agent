"""Cost Guard L1 实现。

职责:
- 在每次 LLM 调用前,基于(estimated_prompt_tokens, max_output_tokens)做硬上限拦截。
- 提供 `estimate_prompt_tokens` 启发式估算,避免对 `tiktoken` 的强依赖。真正的
  tokenizer 替换在 P1-002 扩展 L2 时引入。
- 拒绝时抛 `CostGuardDenied`,带上 level / reason / limits,便于 coordinator 记录
  event 与决定是否触发 handoff 重新规划(P1-004)。

L2 / L3 不在此文件里 —— 它们需要 Event Store 做 daily 累计,不放进 L1 的单调用
同步拦截路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class CostGuardDenied(Exception):
    """Cost Guard 拒绝一次调用时抛出。

    `level` 固定为 L1(当前只有 L1);L2/L3 上线后会在各自的 guard 模块里各自抛。
    `reason` 是给日志 / event store 用的短字符串,`limit_kind` 是触发项。
    """

    def __init__(
        self,
        *,
        level: Literal["L1"],
        limit_kind: Literal["prompt_tokens", "output_tokens", "total_tokens"],
        observed: int,
        limit: int,
        agent_name: str,
    ) -> None:
        self.level = level
        self.limit_kind = limit_kind
        self.observed = observed
        self.limit = limit
        self.agent_name = agent_name
        super().__init__(
            f"CostGuard[{level}] denied {agent_name}: {limit_kind} {observed} > {limit}"
        )


@dataclass(frozen=True, slots=True)
class CostGuardConfig:
    """L1 硬上限。

    默认值偏保守,业务侧按需在 `Settings` 里覆盖。三个字段互相独立:任意一项超限
    即拒绝,不做加权求和(加权逻辑见 L3 平台层 cap)。
    """

    max_prompt_tokens_per_call: int = 8_000
    max_output_tokens_per_call: int = 2_000
    max_total_tokens_per_call: int = 10_000


@dataclass(frozen=True, slots=True)
class CostGuardDecision:
    """Cost Guard 通过时返回的决策记录。

    用在后续 P1-021 Event Store 写入场景:coordinator 把这条 decision 一起落 event,
    重放时能复现"当时允许放行是基于哪条 L1 配额"。
    """

    level: Literal["L1"]
    agent_name: str
    estimated_prompt_tokens: int
    estimated_output_tokens: int

    @property
    def estimated_total_tokens(self) -> int:
        return self.estimated_prompt_tokens + self.estimated_output_tokens


class CostGuard:
    """L1 拦截器。无状态 —— 每次调用只比较入参与 config。

    L2/L3 的累计状态由另外的 guard 对象承接,组合方式为 `CostGuardStack` 依次 authorize。
    这样每一层独立可测,也便于 P1-004 做"L1 放行但 L2 拒绝"的差异化日志。
    """

    def __init__(self, config: CostGuardConfig | None = None) -> None:
        self._config = config or CostGuardConfig()

    @property
    def config(self) -> CostGuardConfig:
        return self._config

    def authorize_call(
        self,
        *,
        agent_name: str,
        estimated_prompt_tokens: int,
        estimated_output_tokens: int,
    ) -> CostGuardDecision:
        """L1 检查。三个上限任一超限即抛 `CostGuardDenied`。

        `agent_name` 只用于日志 / event,不参与判定。入参必须非负,否则上游估算有 bug,
        直接 raise ValueError 让错误显式暴露,不默默放行。
        """
        if estimated_prompt_tokens < 0 or estimated_output_tokens < 0:
            raise ValueError(
                "estimated tokens must be non-negative: "
                f"prompt={estimated_prompt_tokens}, output={estimated_output_tokens}"
            )

        cfg = self._config
        if estimated_prompt_tokens > cfg.max_prompt_tokens_per_call:
            raise CostGuardDenied(
                level="L1",
                limit_kind="prompt_tokens",
                observed=estimated_prompt_tokens,
                limit=cfg.max_prompt_tokens_per_call,
                agent_name=agent_name,
            )
        if estimated_output_tokens > cfg.max_output_tokens_per_call:
            raise CostGuardDenied(
                level="L1",
                limit_kind="output_tokens",
                observed=estimated_output_tokens,
                limit=cfg.max_output_tokens_per_call,
                agent_name=agent_name,
            )
        total = estimated_prompt_tokens + estimated_output_tokens
        if total > cfg.max_total_tokens_per_call:
            raise CostGuardDenied(
                level="L1",
                limit_kind="total_tokens",
                observed=total,
                limit=cfg.max_total_tokens_per_call,
                agent_name=agent_name,
            )

        return CostGuardDecision(
            level="L1",
            agent_name=agent_name,
            estimated_prompt_tokens=estimated_prompt_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )


def estimate_prompt_tokens(text: str) -> int:
    """启发式 token 估算。

    规则(故意粗略):每 4 个 ASCII 字符 ≈ 1 token,每个 CJK 字符 ≈ 1 token。这个估算
    在 L1 hard ceiling 场景足够用 —— 上限本身就是保守值,估算偏高会导致提前拒绝,不会
    放行过大的 prompt。真实 tokenizer(tiktoken / 模型侧计数)在 L2 cost 累计处引入,
    因为 L2 涉及钱的计算,粗估会累积漂移。
    """
    if not text:
        return 0

    ascii_chars = 0
    cjk_chars = 0
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            ascii_chars += 1
        elif 0x3000 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF:
            cjk_chars += 1
        else:
            # 其它(emoji / 拉丁扩展等)按 ASCII 规则估算
            ascii_chars += 1

    return max(1, (ascii_chars + 3) // 4 + cjk_chars)
