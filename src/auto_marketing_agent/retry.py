"""通用重试装饰器(P1-030)。

目标:
- 给幂等或可接受重放的异步调用加指数退避重试。
- 默认参数与 OpenAI API 推荐对齐:最多 3 次、基准 0.5s、倍率 2.0、抖动 ±25%。
- 只重试显式声明的异常类型,**不要** 把 `CostGuardDenied`、`ValueError`、校验异常
  当成可重试错误 —— 这些是业务拒绝,重试只会放大问题。

不在本文件内覆盖:
- DLQ(死信队列)流转 → P1-031 / P1-032。
- Sandbox 崩溃自愈 → P1-033,那里需要更重的 lifecycle 管理。

Usage(同步 / 异步均可):

    @retry(max_attempts=3, retry_on=(httpx.HTTPError, TimeoutError))
    async def fetch_meta_insight(...):
        ...
"""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import ParamSpec, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """重试策略。

    `base_delay_seconds` 是第一次重试前等待的基准时长;后续延时 =
    `base_delay * (multiplier ** attempt)`,
    再叠加 ±`jitter_ratio` 的抖动。`max_attempts` 含首次尝试(= 首次 + 重试次数之和)。
    """

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    multiplier: float = 2.0
    jitter_ratio: float = 0.25
    max_delay_seconds: float = 30.0


def _compute_delay(policy: RetryPolicy, attempt_index: int, rng: random.Random) -> float:
    """第 n 次失败后、第 n+1 次尝试前应等待的秒数。

    `attempt_index` 从 0 开始:attempt_index=0 代表"首次失败后、第 1 次重试前"。
    """
    raw = policy.base_delay_seconds * (policy.multiplier**attempt_index)
    capped = min(raw, policy.max_delay_seconds)
    if policy.jitter_ratio <= 0:
        return capped
    jitter = capped * policy.jitter_ratio
    return max(0.0, capped + rng.uniform(-jitter, jitter))


def retry(
    *,
    max_attempts: int = 3,
    retry_on: tuple[type[BaseException], ...],
    base_delay_seconds: float = 0.5,
    multiplier: float = 2.0,
    jitter_ratio: float = 0.25,
    max_delay_seconds: float = 30.0,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    rng: random.Random | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """装饰器工厂。

    `retry_on` **强制显式传入**,没有默认值。这样做是为了避免"随手一装"把 ValueError /
    CostGuardDenied 也跟着重试 —— 那种静默重试是生产事故的温床。

    `sleep` / `rng` 是注入点:测试时用 `sleep=lambda _: asyncio.sleep(0)` 把等待压到零,
    `rng=random.Random(seed)` 让抖动可复现。

    支持同步与异步函数;同步函数内部仍用 `time.sleep` 会阻塞线程,建议只装饰异步 I/O。
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if not retry_on:
        raise ValueError("retry_on must list at least one exception type")

    policy = RetryPolicy(
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        multiplier=multiplier,
        jitter_ratio=jitter_ratio,
        max_delay_seconds=max_delay_seconds,
    )
    sleep_fn = sleep or asyncio.sleep
    random_source = rng or random.Random()

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                last_exc: BaseException | None = None
                for attempt in range(policy.max_attempts):
                    try:
                        result = await func(*args, **kwargs)
                        return cast(R, result)
                    except retry_on as exc:
                        last_exc = exc
                        if attempt + 1 >= policy.max_attempts:
                            break
                        delay = _compute_delay(policy, attempt, random_source)
                        await sleep_fn(delay)
                assert last_exc is not None
                raise last_exc

            return cast(Callable[P, R], async_wrapper)

        @wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # 同步路径故意不支持自定义 sleep —— 同步函数里用 asyncio.sleep 无意义,
            # 让同步调用方直接用 time.sleep 语义;测试则应走 async 装饰 + fake sleep。
            import time

            last_exc: BaseException | None = None
            for attempt in range(policy.max_attempts):
                try:
                    return func(*args, **kwargs)
                except retry_on as exc:
                    last_exc = exc
                    if attempt + 1 >= policy.max_attempts:
                        break
                    delay = _compute_delay(policy, attempt, random_source)
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return sync_wrapper

    return decorator
