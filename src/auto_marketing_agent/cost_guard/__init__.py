"""Cost Guard —— LLM 调用的三层预算拦截。

P1-001 实现 L1(单调用 ceiling),P1-002 实现 L2(单 campaign 单日 cap,基于
Event Store 聚合)。L3(平台层日 cap)排 P1-003。

架构约束(CLAUDE.md):每次 LLM 调用前必须过 Cost Guard。coordinator 在调 Runner
前按序过 L1 → L2;任一层否决都直接抛 `CostGuardDenied`,由上层决定(L1 可压
brief 重规划,L2 要等次日或走 HITL 增额)。
"""

from auto_marketing_agent.cost_guard.guard import (
    CostGuard,
    CostGuardConfig,
    CostGuardDecision,
    CostGuardDenied,
    DailyCostConfig,
    DailyCostGuard,
    estimate_prompt_tokens,
)

__all__ = [
    "CostGuard",
    "CostGuardConfig",
    "CostGuardDecision",
    "CostGuardDenied",
    "DailyCostConfig",
    "DailyCostGuard",
    "estimate_prompt_tokens",
]
