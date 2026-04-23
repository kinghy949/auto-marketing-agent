# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指引。

## 语言规则(强制)

**本仓库的所有产出统一使用中文**,包括但不限于:

- 与用户的对话回复
- 文档(README、`docs/` 下所有文件、设计稿、ADR)
- 代码注释、docstring、日志文本
- commit message 正文与 PR 描述
- 变量/函数名以外的所有自然语言文本

例外:技术专有名词(如 `Sandbox Agent`、`Handoff`、`MMM`、`ROAS`)、第三方 API 字段名、代码标识符保持英文原文。

> 英文原文(供翻译/对照): All artifacts in this repository must be written in Chinese, including conversation replies, documentation (README, everything under `docs/`, design notes, ADRs), code comments, docstrings, log strings, commit message bodies, and PR descriptions. Exception: technical proper nouns, third-party API field names, and code identifiers remain in their original English form.

## 仓库当前状态

P0 MVP 骨架已闭环,P1 离线部分(Cost Guard L1 / 重试装饰器 / Guardrail)已落地,剩余阻塞项只有 P0-021(Meta Ads OAuth,需真实开发者账号):

- **Schema Registry**(`src/auto_marketing_agent/schemas/`):7 条 v1 Pydantic schema + `registry.py` 查表
- **Secrets 抽象 + scoped view**(`src/auto_marketing_agent/secrets/`):env 后端 + 审计接口,设计文档见 `docs/scoped-credentials.md`
- **Tracing 封装**(`src/auto_marketing_agent/tracing.py`):`campaign_trace` 固定 SDK `group_id = campaign_id`
- **CI/CD**(`.github/workflows/`):ci(ruff + mypy + pytest)+ security(gitleaks + pip-audit)
- **业务 agent**(`src/auto_marketing_agent/agents/`):
  - Orchestrator(`orchestrator.py`)→ CampaignPlan 结构化解析
  - Audience(`audience.py`)+ mock CDP 工具(`tools/cdp_mock.py`)
  - Creative(`creative.py`)+ mock Asset Library(`tools/asset_library_mock.py`)
  - Guardrail(`guardrail.py`)+ 机审工具(`tools/guardrail_check.py`)
  - Coordinator(`coordinator.py`)Python 层串三段并做 correlation/campaign/segment ID 一致性校验
- **Cost Guard L1 / L2 / L3**(`src/auto_marketing_agent/cost_guard/`):
  - L1 `CostGuard.authorize_call` 拦截单次调用 token 上限,coordinator 每步 Runner.run 前先过;Audience / Creative 阶段被 L1 拒时把拒绝上下文追加到 brief 重跑一次(`max_cost_replans` 控制)
  - L2 `DailyCostGuard.authorize_call` 聚合 Event Store 内当日 `cost_guard.authorized` 事件算单 campaign 累计 token,超上限抛 `CostGuardDenied(level="L2")`
  - L3 `PlatformDailyCostGuard.authorize_call` 同源聚合但不按 campaign 过滤,包含 Orchestrator 阶段 `campaign_id=None` 的事件,守平台单日 token 总闸;coordinator 顺序 L1 → L2 → L3,三层都过才发 `cost_guard.authorized`,避免"前层过后层拒"的虚耗污染后续累计
  - L2 / L3 拒绝都不走 brief 重规划(累计量不因缩 prompt 而下降,只能等次日或走 HITL 增额)
- **通用重试装饰器**(`src/auto_marketing_agent/retry.py`):`@retry(retry_on=(...))` 指数退避 + 抖动,同步 / 异步共用
- **Guardrail 规则引擎**(`src/auto_marketing_agent/guardrail/`):品牌词典 + 中国广告法绝对化 / 医疗 + 欧盟基础比较 / 儿童宣传 4 类规则,`evaluate_variant` 产出 ApprovalDecision
- **HITL 内存队列**(`src/auto_marketing_agent/hitl/`):`InMemoryHitlQueue` 幂等入队 / resolve 状态机,coordinator 在 Guardrail 产出 `needs_hitl` 时自动入队,生产后端留 P2
- **Event Store**(`src/auto_marketing_agent/events/` + `migrations/001_events.sql`):`EventStore` Protocol + 三挡后端 `InMemoryEventStore` / `JsonlEventStore`(append-only JSONL,重放时按 occurred_at 升序)/ `PostgresEventStore`(psycopg3 同步 + `ConnectionPool`,装 `pip install -e '.[postgres]'` 才进路径;DB 层 `events_reject_mutation` 触发器兜底 append-only),coordinator 在 cost_guard(authorized/denied)、guardrail.evaluated、creative.rejected、hitl.enqueued、cost_replan.triggered、dlq.enqueued、campaign.completed 八个位点发射事件;CLI `events replay` 支持按 campaign / correlation / event_type / time-range 组合过滤;本地起 Postgres 用 `docker compose up -d postgres`,集成测试由 `AMA_TEST_POSTGRES_DSN` 环境变量控流
- **DLQ**(`src/auto_marketing_agent/dlq/` + `migrations/002_dlq_items.sql`):`DlqQueue` Protocol + 双后端 `InMemoryDlq` / `PostgresDlq`(psycopg3 同步,装 `[postgres]` 才进路径;CHECK 约束守 pending ↔ resolved 字段一致性 + partial index on pending),coordinator 拦 SDK `ModelBehaviorError`(schema 反序列化失败)入队并抛 `SchemaDeserializationFailed`;resolve 状态机 pending → replayed/discarded,Postgres 后端并发 resolve 走行锁 + WHERE status='pending' + 二次 SELECT 区分 NotFound / AlreadyResolved
- **CLI**:`auto-marketing-agent run --brief "..." [--correlation-id ...] [--model ...]` 一次吐出三份 payload + 事件计数的 JSON
- **Docker 化**(`Dockerfile` + `docker-compose.yml`):多阶段构建、非 root 运行、CI 里加 `docker-build` 验证 `--help`
- **Golden cases**(`tests/golden/`):5 条 Audience + 5 条 Creative,`tests/test_golden_cases.py` 作 CI 阻塞回归

未实现:Media Buyer / Attribution / Experiment 三个 agent,以及 Circuit Breaker、Data Layer、Knowledge Store 三个平台组件,以及 HITL 的 Postgres 持久化后端(Event Store / DLQ 的已落地),全部排在 P1+。事件驱动 handoff(Attribution → Creative 回炉)留 P2。

技术栈决策见 [`docs/adr/0001-tech-stack.md`](docs/adr/0001-tech-stack.md);任务追踪见 [`docs/tasks.md`](docs/tasks.md)。

### 常用命令

使用 `uv`(推荐):

```bash
uv sync --all-extras            # 安装运行时 + dev 依赖
uv run ruff check .             # lint
uv run ruff format .            # 格式化
uv run mypy src tests           # 类型检查(strict)
uv run pytest                   # 单元测试
uv run auto-marketing-agent hello  # 跑 hello 烟雾测试(需 OPENAI_API_KEY)
```

不装 `uv` 的回退路径:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff check . && ruff format . && mypy src tests && pytest
```

运行前把 `.env.example` 复制为 `.env` 并填 `OPENAI_API_KEY`。

**Docker 路径**(P1-060):

```bash
docker compose build
docker compose run --rm app --help
docker compose run --rm app run --brief "..."
```

## 设计文档位置

- `README.md` —— 项目意图、目标用户、MVP 阶段路线图(P0 Copilot → P3 规模化)。
- `docs/architecture.md` —— 权威系统设计:agent 拓扑、各 agent 的职责/工具/输出、sandbox 用法、session 模型、handoff 拓扑、human-in-the-loop 插点、已知难题。

**在提出结构性改动前先读 `docs/architecture.md`** —— 很多"看起来明显"的简化方案(用线性流水线替代事件驱动 handoff、mock 掉归因、跳过 Guardrail)都已被评估并基于明确理由否决。

## 已敲定的架构约束

以下是**决策**,不是建议。代码落地时应遵守。详见 `docs/architecture.md` (v2)。

**Agent 层:**
- **基于 OpenAI Agents SDK** (`openai-agents`)。多 agent 编排使用该 SDK 的原语(Agents、Sandbox Agents、Tools、Handoffs、Sessions、Tracing),不要自造框架。
- **事件驱动 handoff,而非线性流水线**。Attribution agent 可在 ROAS 下滑时主动 handoff 回 Creative agent。
- **每个 Campaign 一个 Session**,生命周期约 4–6 周。跨 campaign 的知识走 Knowledge Store,不要塞 Session。
- **长任务必须放进 Sandbox Agent**(ffmpeg/PIL 媒体处理、MMM 模型拟合)。Sandbox 内只能拿 scoped credentials。
- **MMM 是必备,不是可选**。iOS 14 之后 MTA 不可信,不要把点击归因当作 ground truth 来设计。

**平台服务层(一等公民,不是附属):**
- **Schema Registry**:所有 handoff payload 用 Pydantic 强类型,集中版本化。反序列化失败 → DLQ,不要降级为 dict 直接传递。
- **Cost Guard**:三层 token 预算(单调用 / 单 campaign 单日 / 平台层 daily cap)。每次 LLM 调用前必须过 Cost Guard。
- **Circuit Breaker 独立于 HITL**:HITL 是事前审批,Breaker 是事后熔断。两者都要,不可互相替代。
- **Event Store**:每个 agent decision 写一条 append-only event,支持重放与审计。不要直接修改状态,要走 event。
- **Secrets 必须 scoped**:Sandbox / Media Buyer 拿到的凭证必须是最小权限子集,不要用 full-access token。
- **Data Layer 必须做 freshness check**:Attribution 拿到滞后 > 6h 的数据时必须降级或拒绝出报告,联动 Circuit Breaker。

**HITL 边界:**
- 仅当单日预算变化 > 30%、涉及新品类/敏感词、或 Circuit Breaker 触发后 resume 时强制人工。不要"为了保险"在其他地方加审批,会破坏自治目标。

**部署:**
- MVP 阶段单租户独立部署(每客户独立 namespace + DB + Vault path)。客户数 > 20 之前不要做共享多租户。

## Commit 规范

每个 commit message 末尾必须带尾注(trailer):

```
Co-Authored-By: Claude <noreply@anthropic.com>
```

必须使用此精确格式(不是 harness 默认的 `Claude Opus X.X`)。请用 heredoc 传递 commit message 以保留格式。
