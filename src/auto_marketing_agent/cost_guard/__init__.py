"""Cost Guard —— LLM 调用的三层预算拦截。

P1 只实现 L1(单调用 ceiling)。L2(单 campaign 单日 cap)与 L3(平台层日 cap)需要
Event Store 累计,排在 P1-002 / P1-003。

架构约束(CLAUDE.md):每次 LLM 调用前必须过 Cost Guard。coordinator 层在调用 Runner
之前调用 `CostGuard.authorize_call()`;若返回否决则直接抛异常,由上层 handoff /
重试逻辑接管。
"""

from auto_marketing_agent.cost_guard.guard import (
    CostGuard,
    CostGuardConfig,
    CostGuardDecision,
    CostGuardDenied,
    estimate_prompt_tokens,
)

__all__ = [
    "CostGuard",
    "CostGuardConfig",
    "CostGuardDecision",
    "CostGuardDenied",
    "estimate_prompt_tokens",
]
