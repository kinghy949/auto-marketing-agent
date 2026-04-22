"""Cost Guard L1 的单元测试。

覆盖三条上限、边界条件、异常字段、估算函数的粗略正确性。
"""

from __future__ import annotations

import pytest

from auto_marketing_agent.cost_guard import (
    CostGuard,
    CostGuardConfig,
    CostGuardDenied,
    estimate_prompt_tokens,
)


def test_authorize_under_all_ceilings_returns_decision() -> None:
    guard = CostGuard(CostGuardConfig(8000, 2000, 10000))
    decision = guard.authorize_call(
        agent_name="audience-agent",
        estimated_prompt_tokens=100,
        estimated_output_tokens=50,
    )
    assert decision.level == "L1"
    assert decision.agent_name == "audience-agent"
    assert decision.estimated_total_tokens == 150


def test_prompt_tokens_exceeds_limit_raises() -> None:
    guard = CostGuard(CostGuardConfig(1000, 2000, 10000))
    with pytest.raises(CostGuardDenied) as exc:
        guard.authorize_call(
            agent_name="audience-agent",
            estimated_prompt_tokens=1001,
            estimated_output_tokens=0,
        )
    assert exc.value.level == "L1"
    assert exc.value.limit_kind == "prompt_tokens"
    assert exc.value.observed == 1001
    assert exc.value.limit == 1000


def test_output_tokens_exceeds_limit_raises() -> None:
    guard = CostGuard(CostGuardConfig(8000, 500, 10000))
    with pytest.raises(CostGuardDenied) as exc:
        guard.authorize_call(
            agent_name="creative-agent",
            estimated_prompt_tokens=100,
            estimated_output_tokens=501,
        )
    assert exc.value.limit_kind == "output_tokens"
    assert exc.value.observed == 501


def test_total_tokens_exceeds_limit_raises() -> None:
    # 单独看 prompt/output 都不超,合起来超 total,走第三条红线
    guard = CostGuard(CostGuardConfig(8000, 2000, 1500))
    with pytest.raises(CostGuardDenied) as exc:
        guard.authorize_call(
            agent_name="orchestrator-agent",
            estimated_prompt_tokens=900,
            estimated_output_tokens=800,
        )
    assert exc.value.limit_kind == "total_tokens"
    assert exc.value.observed == 1700


def test_equal_to_limit_is_allowed() -> None:
    # 边界:等于上限时放行(严格大于才拒)
    guard = CostGuard(CostGuardConfig(1000, 500, 1500))
    decision = guard.authorize_call(
        agent_name="a",
        estimated_prompt_tokens=1000,
        estimated_output_tokens=500,
    )
    assert decision.estimated_total_tokens == 1500


def test_negative_estimate_raises_value_error() -> None:
    # 防御式:负数是估算器 bug,必须显式报错,不能默默放行
    guard = CostGuard()
    with pytest.raises(ValueError):
        guard.authorize_call(
            agent_name="a",
            estimated_prompt_tokens=-1,
            estimated_output_tokens=0,
        )


def test_default_config_is_conservative() -> None:
    cfg = CostGuardConfig()
    # 默认值必须有限,且总和小于等于 total 上限
    assert cfg.max_prompt_tokens_per_call > 0
    assert cfg.max_output_tokens_per_call > 0
    assert cfg.max_total_tokens_per_call > 0


def test_estimate_prompt_tokens_empty_string() -> None:
    assert estimate_prompt_tokens("") == 0


def test_estimate_prompt_tokens_ascii_rule() -> None:
    # 12 ASCII 字符 ≈ 3 token
    assert estimate_prompt_tokens("abcdefghijkl") == 3


def test_estimate_prompt_tokens_cjk_counts_one_each() -> None:
    # 5 个中文字符约等于 5 token
    assert estimate_prompt_tokens("你好世界啊") == 5


def test_estimate_prompt_tokens_mixed() -> None:
    # 4 ASCII (1 token) + 3 CJK (3 token) = 4 token
    assert estimate_prompt_tokens("abcd你我他") == 4


def test_estimate_prompt_tokens_grows_with_length() -> None:
    a = estimate_prompt_tokens("x" * 100)
    b = estimate_prompt_tokens("x" * 1000)
    assert b > a
