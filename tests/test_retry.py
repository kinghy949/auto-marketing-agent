"""通用重试装饰器测试(P1-030)。

用注入式 `sleep` 把所有等待压到 0,保证测试秒级完成且确定性。
"""

from __future__ import annotations

import random

import pytest

from auto_marketing_agent.retry import RetryPolicy, _compute_delay, retry


async def _noop_sleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_async_retry_eventually_succeeds() -> None:
    calls = {"n": 0}

    @retry(max_attempts=3, retry_on=(RuntimeError,), sleep=_noop_sleep)
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_async_retry_exhausts_and_raises_last_exception() -> None:
    @retry(max_attempts=2, retry_on=(RuntimeError,), sleep=_noop_sleep)
    async def always_fail() -> None:
        raise RuntimeError("still bad")

    with pytest.raises(RuntimeError, match="still bad"):
        await always_fail()


@pytest.mark.asyncio
async def test_retry_does_not_catch_unlisted_exceptions() -> None:
    # retry_on=(TimeoutError,) 不应 catch ValueError
    @retry(max_attempts=5, retry_on=(TimeoutError,), sleep=_noop_sleep)
    async def bad() -> None:
        raise ValueError("business rejection")

    with pytest.raises(ValueError):
        await bad()


def test_sync_retry_succeeds_after_failures() -> None:
    counter = {"n": 0}

    @retry(
        max_attempts=3,
        retry_on=(RuntimeError,),
        base_delay_seconds=0,
        jitter_ratio=0,
    )
    def flaky() -> int:
        counter["n"] += 1
        if counter["n"] < 2:
            raise RuntimeError("boom")
        return 42

    assert flaky() == 42


def test_retry_on_empty_tuple_raises_at_decoration_time() -> None:
    with pytest.raises(ValueError, match="retry_on"):
        retry(max_attempts=3, retry_on=())


def test_max_attempts_below_one_raises() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        retry(max_attempts=0, retry_on=(RuntimeError,))


def test_compute_delay_is_deterministic_with_seeded_rng() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=1.0, multiplier=2.0, jitter_ratio=0.2)
    rng = random.Random(42)
    delays_first = [_compute_delay(policy, i, rng) for i in range(3)]
    rng2 = random.Random(42)
    delays_second = [_compute_delay(policy, i, rng2) for i in range(3)]
    assert delays_first == delays_second


def test_compute_delay_respects_max_cap() -> None:
    policy = RetryPolicy(
        max_attempts=10,
        base_delay_seconds=5.0,
        multiplier=10.0,
        jitter_ratio=0,
        max_delay_seconds=12.0,
    )
    rng = random.Random(0)
    # 第 2 次(index=2)原始值 = 5 * 10^2 = 500,必须被截到 12
    assert _compute_delay(policy, 2, rng) == 12.0


@pytest.mark.asyncio
async def test_async_retry_passes_args_and_kwargs() -> None:
    @retry(max_attempts=2, retry_on=(RuntimeError,), sleep=_noop_sleep)
    async def echo(a: int, *, b: str) -> str:
        return f"{a}-{b}"

    assert await echo(7, b="x") == "7-x"
