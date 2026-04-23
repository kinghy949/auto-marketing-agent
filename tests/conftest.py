"""全局 pytest fixtures。

Postgres 集成测试用 `pg_dsn` —— 读 `AMA_TEST_POSTGRES_DSN` 环境变量,没设就 skip。
这样做而不是 testcontainers:
- 本地开发者跑 `docker compose up -d postgres` 后 `export AMA_TEST_POSTGRES_DSN=...`
  就能跑,不必在每次测试时启/停容器;
- CI 可在 GitHub Actions 用 `services.postgres` + 导出同名环境变量,无需改测试代码;
- 不把 testcontainers 拉进 dev 依赖,离线 / CI 默认路径(只跑 InMemory)不被拖慢。

注意:所有命中真实 DB 的测试共享同一张 `events` / `hitl_items` / `dlq_items` 表,
测试之间用"随机命名空间(uuid 前缀的 correlation_id)"做逻辑隔离,不清表。理由:
跨用例清表会放大并行测试的串扰风险,换成命名空间反而简单可预测。
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get("AMA_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AMA_TEST_POSTGRES_DSN 未设置,跳过 Postgres 集成测试")
    return dsn
