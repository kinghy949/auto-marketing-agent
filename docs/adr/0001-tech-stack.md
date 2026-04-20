---
id: ADR-0001
title: 技术栈选型(Python 版本、包管理、Lint、测试)
status: Accepted
date: 2026-04-20
---

# ADR-0001 技术栈选型

## 背景

仓库从设计阶段进入代码阶段,需要在落下第一行运行时代码前把技术栈敲定,避免后续返工。`CLAUDE.md` 已明确"代码落地前不要凭空编造 build/lint/test 命令",本 ADR 负责把这些命令定下来。

选型范围仅限 P0 骨架必需的基础工具链,**不**包含运行时依赖(`openai-agents`、`pydantic` 等由各任务在引入时单独记录)、**不**包含基础设施选型(Postgres / Vault / Vector DB 另立 ADR)。

## 决策

| 维度 | 选定 | 版本约束 |
|------|------|---------|
| 语言 | Python | `>=3.10,<3.13` |
| 打包规范 | PEP 621 `pyproject.toml` + `src/` 布局 | — |
| 包/虚拟环境管理 | `uv` | `>=0.4` |
| Lint + Formatter | `ruff` | `>=0.6` |
| 类型检查 | `mypy` | `>=1.10`,`strict = true` |
| 测试框架 | `pytest` + `pytest-asyncio` | `pytest>=8`,`pytest-asyncio>=0.23` |
| 配置加载 | `pydantic-settings` | `>=2.4` |

## 理由

**Python 3.10 下限**:`openai-agents` SDK 官方要求 Python 3.9+,但我们依赖的 `match` 语句、精确类型别名、PEP 604 union 语法都是 3.10+ 才稳定。上限锁 3.13 是因为 MMM 相关科学计算栈(PyMC / Robyn 的 Python 绑定)在 3.13 尚未完成适配,留到 P2 再评估解锁。

**`uv` 而非 `poetry` / `pip-tools`**:

- 装包速度比 `pip` 快 10–100 倍,CI 成本直接下降
- 原生支持 `pyproject.toml` + lockfile,不引入额外配置文件
- 作者(Astral,即 `ruff` 的作者)把 `uv` 和 `ruff` 作为一套设计,工具链心智一致
- `poetry` 的 `poetry.lock` 格式非标准,迁移成本高;`uv` 的 lockfile 基于 PEP 标准提案

风险:`uv` 1.0 尚未发布,API 可能变化。对冲方案是不依赖 `uv` 独有特性,`pyproject.toml` 保证用 `pip install -e .` 也能装,仅牺牲速度。

**`ruff` 一统 lint + format**:替代 `flake8` + `isort` + `black` 三件套,单一工具配置,速度是 Rust 级的。规则集启用 `E,F,I,B,UP,SIM,RUF` 即覆盖 90% 常见问题。

**`mypy` 严格模式**:Handoff payload 是 Pydantic 强类型(架构已定),再加 `mypy --strict` 形成编译期/类型期双重防线,能把 P1 阶段 Schema 反序列化失败引发的 DLQ 数量压到最低。不选 `pyright` 的原因是 CI 里 Node 运行时不必要,而且 `pydantic` 对 `mypy` 的插件支持更成熟。

**`pytest` + `pytest-asyncio`**:`openai-agents` SDK 是 async-first,测试必须原生支持 async。`unittest` 对 async 的支持要靠 `asyncSetUp` 打补丁,不如 `pytest-asyncio` 自然。

**`pydantic-settings`**:`.env` 加载走 Pydantic 模型,和 Schema Registry 的 Pydantic 生态一致,避免引入第二套配置体系(如 `dynaconf` / `python-decouple`)。

## 项目布局约定

采用 `src/` layout 而非顶级包布局,理由:

- 避免测试时误 import 当前目录下未安装的包
- 与编辑器/CI 行为一致:必须显式 `pip install -e .` 才能跑测试,强制走安装路径

```
auto-marketing-agent/
├── pyproject.toml
├── uv.lock               # uv 生成,提交
├── LICENSE
├── README.md
├── CLAUDE.md
├── .env.example
├── .gitignore
├── .python-version       # 锁定 3.10
├── src/
│   └── auto_marketing_agent/
│       ├── __init__.py
│       ├── __main__.py   # CLI 入口(P0-033 填充)
│       └── agents/
│           └── __init__.py
├── tests/
│   └── __init__.py
└── docs/
    ├── architecture.md
    ├── tasks.md
    └── adr/
        └── 0001-tech-stack.md  # 本文件
```

## 命令一览(落地后生效)

```bash
# 安装依赖(含开发依赖)
uv sync --all-extras

# Lint + 格式化
uv run ruff check .
uv run ruff format .

# 类型检查
uv run mypy src tests

# 测试
uv run pytest
```

回退路径(不装 `uv` 的环境):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff check . && ruff format . && mypy src tests && pytest
```

## 后果

**正面**

- CI 冷启动 < 30 秒即可完成依赖安装(`uv` 实测)
- `ruff` + `mypy --strict` 组合对 Schema Registry 的强类型约束形成天然支撑
- `src/` layout 避免"测试时 import 到错版本"的隐形 bug

**负面与缓解**

- `uv` 尚未 1.0,有 breaking 风险 → 不依赖 `uv` 独有字段,`pip` 路径保留
- `mypy strict` 会显著增加 P0 写代码的摩擦 → 接受,因为 agent handoff 类型错误的代价远高于 mypy 写注解的代价
- Python 上限 3.12 意味着 MMM 相关依赖到 P2 可能被迫升级 → 届时重开 ADR 评估

## 关联任务

- 本 ADR 完成 → 关闭 X-005
- 后续 X-006(CI lint + test)直接复用本 ADR 的命令
- P0-001(`pyproject.toml`)按本 ADR 的布局落地
