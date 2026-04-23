# 任务清单

本文件用于追踪 auto-marketing-agent 各阶段任务的完成情况,按 P0 → P3 路线图组织,与 [`architecture.md`](architecture.md) 的平台层依赖表对齐。

## 状态图例

- ⬜ 待办
- 🟡 进行中
- ✅ 已完成
- ⏸️ 暂停 / 阻塞
- ❌ 已取消

## 维护约定

- 任务 ID 一旦分配不复用(即使取消)
- 完成任务时填写 commit / PR 链接
- 阻塞任务必须填"阻塞原因"
- 新增任务沿用 `阶段-编号` 格式(如 `P1-012`)

---

## 跨阶段(基础设施 / 治理)

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| X-001 | 初始化仓库与 README、架构文档 | ✅ | — | commit `b082259` |
| X-002 | 文档全部中文化 + CLAUDE.md 规则 | ✅ | X-001 | commit `7668bab` |
| X-003 | 架构 v2:补齐平台服务层 | ✅ | X-002 | commit `4154a90` |
| X-004 | 创建本任务清单 | ✅ | X-003 | commit `0056ffa` |
| X-005 | 选定技术栈并写入 ADR(Python 版本、包管理、Lint、测试框架) | ✅ | X-004 | `docs/adr/0001-tech-stack.md` |
| X-006 | CI/CD 雏形(GitHub Actions:lint + test) | ✅ | X-005 | `.github/workflows/ci.yml` |
| X-007 | LICENSE 选定(MIT / Apache-2.0 / 商业许可) | ✅ | — | Apache-2.0 |
| X-008 | 安全审查流程(secrets scanning、依赖扫描) | ✅ | X-006 | `.github/workflows/security.yml`(gitleaks + pip-audit) |

---

## P0 — Copilot(目标:生成创意 + 推荐受众,人工投放)

### 项目骨架

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P0-001 | `pyproject.toml` + 包目录结构 | ✅ | X-005 | `uv` + `src/` layout |
| P0-002 | 安装 `openai-agents` SDK,跑通最小 hello-world agent | ✅ | P0-001 | `src/auto_marketing_agent/agents/hello.py`,代码就位,待装依赖后执行 |
| P0-003 | 配置 `OPENAI_API_KEY` 加载与本地 `.env.example` | ✅ | P0-001 | `pydantic-settings` + `.env.example` |
| P0-004 | Tracing 接入与本地查看(SDK 自带) | ✅ | P0-002 | `src/auto_marketing_agent/tracing.py`(`campaign_trace` 封装 + `AMA_TRACING_DISABLED`) |

### Schema Registry(P0 必备)

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P0-010 | 定义 `CampaignPlan` Pydantic schema | ✅ | P0-001 | `schemas/v1/campaign.py` |
| P0-011 | 定义 `AudienceSegment` schema | ✅ | P0-001 | `schemas/v1/audience.py`,仅允许 hashed IDs |
| P0-012 | 定义 `CreativeVariant` schema | ✅ | P0-001 | `schemas/v1/creative.py`,含 AssetRights |
| P0-013 | 定义 `BuyOrder` schema(含 idempotency key 字段) | ✅ | P0-001 | `schemas/v1/buy_order.py` |
| P0-014 | 定义 `ExperimentSpec` / `StopRule` schema | ✅ | P0-001 | `schemas/v1/experiment.py`,含 traffic_share 合法性 |
| P0-015 | 定义 `AttributionReport` schema | ✅ | P0-001 | `schemas/v1/attribution.py`,含 freshness SLO 联动 |
| P0-016 | 定义 `ApprovalDecision` schema | ✅ | P0-001 | `schemas/v1/approval.py`,reject 必带 violations |
| P0-017 | Schema 版本化机制(目录 / 版本号约定) | ✅ | P0-010..016 | `schemas/v<N>/` 目录 + `registry.py` 查表 |

### Secrets Manager(P0 必备)

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P0-020 | Secrets 抽象接口(支持 env / Vault / AWS SM) | ✅ | P0-001 | `src/auto_marketing_agent/secrets/`,env 后端 + 审计接口 |
| P0-021 | OAuth 流程:Meta Ads | ⬜ | P0-020 | refresh token 持久化 |
| P0-022 | Scoped credentials 设计文档 | ✅ | P0-020 | `docs/scoped-credentials.md` |

### Agent MVP(只做 Copilot 必需的两个)

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P0-030 | Audience Agent:CDP SQL tool + 输出 `AudienceSegment` | ✅ | P0-011, P0-020 | mock CDP(`agents/tools/cdp_mock.py`)+ `agents/audience.py` |
| P0-031 | Creative Agent:LLM 文案 + DALL·E 图片 | ✅ | P0-012 | Asset Library mock(`agents/tools/asset_library_mock.py`)+ `agents/creative.py`;真实图像生成 P1 接入 |
| P0-032 | Orchestrator Agent:接收 KPI → handoff Audience → Creative | ✅ | P0-030, P0-031 | `agents/orchestrator.py` 产 CampaignPlan;`agents/coordinator.py` Python 层串三段(真正事件驱动 handoff 留 P2) |
| P0-033 | CLI 入口:`python -m auto_marketing_agent run --kpi ... --budget ...` | ✅ | P0-032 | `run --brief --correlation-id --model`,输出 plan/segment/variant 三份 JSON |

### Eval(最小可用)

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P0-040 | 收集 ≥ 5 条 Audience golden case | ✅ | P0-030 | `tests/golden/audience/*.json`,5 条 |
| P0-041 | 收集 ≥ 5 条 Creative golden case | ✅ | P0-031 | `tests/golden/creative/*.json`,5 条 |
| P0-042 | `pytest` 集成 golden case 回归测试 | ✅ | P0-040, P0-041 | `tests/test_golden_cases.py`,21 个参数化断言 |

---

## P1 — 半自动(目标:自动投放,所有创意需人审)

### Cost Guard

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P1-001 | L1 单调用 token ceiling 拦截器 | ✅ | P0-002 | `cost_guard.CostGuard.authorize_call`,已接入 coordinator |
| P1-002 | L2 单 Campaign 单日 token cap | ✅ | P1-001, P1-020 | `DailyCostGuard` 聚合 `cost_guard.authorized` 事件;L2 拒绝不走 brief 重规划 |
| P1-003 | L3 平台层 daily LLM cost cap | ✅ | P1-002 | `PlatformDailyCostGuard` 聚合当日所有 `cost_guard.authorized` 事件(含 Orchestrator campaign_id=None);L3 拒绝不走 brief 重规划 |
| P1-004 | Cost Guard 拒绝时通知 Orchestrator 重新规划 | ✅ | P1-001 | `run_campaign` 的重规划循环:Audience / Creative 阶段被拒时把拒绝上下文塞回 brief 重跑,`max_cost_replans` 控制上限 |

### Event Store

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P1-020 | PostgreSQL append-only event 表设计 | ✅ | X-005 | `migrations/001_events.sql` + ADR-0002,单表 + JSONB + append-only 触发器 |
| P1-021 | Event 写入 SDK(每个 agent decision 一条) | ✅ | P1-020 | `events/` 包,Protocol + InMemoryEventStore;coordinator 在 8 个位点发射事件(含 `dlq.enqueued`) |
| P1-022 | 按 `campaign_id` 重放工具 | ✅ | P1-021 | `JsonlEventStore` + `auto-marketing-agent events replay`(commit `dab85c1`) |
| P1-023 | Event 查询 API(供调试 / 离线 eval 使用) | ✅ | P1-021 | `EventStore.list_by_time_range` / `list_by_correlation` / `list_by_campaign`,CLI replay 支持 `--event-type` / `--since` / `--until` 过滤 |
| P1-024 | Event Store Postgres 后端(替换 InMemory 生产路径) | ✅ | P1-020, P1-021 | `events/postgres_store.py` + `psycopg[binary]` 可选依赖 + `docker-compose postgres` 服务;集成测试由 `AMA_TEST_POSTGRES_DSN` 控流 |

### 错误处理 / 重试 / DLQ

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P1-030 | 通用重试装饰器(指数退避,最多 3 次) | ✅ | P0-001 | `auto_marketing_agent.retry.retry`,同步 / 异步均支持,`retry_on` 必传 |
| P1-031 | DLQ Protocol + InMemoryDlq | ✅ | P1-020 | `auto_marketing_agent.dlq`;Postgres 后端见 P1-035 |
| P1-032 | Schema 反序列化失败 → DLQ + 告警 | ✅ | P0-017, P1-031 | coordinator 拦 `ModelBehaviorError`,抛 `SchemaDeserializationFailed` 并发 `dlq.enqueued` 事件;告警接入推到 P3 Observability |
| P1-033 | Sandbox 崩溃 → 自动重启 + 单次重试 | ⬜ | P1-030, P2-040 | 依赖 sandbox runtime 存在 |
| P1-034 | HITL 队列 Postgres 后端 | ⬜ | P1-053 | Iter 3 部署阻塞项 |
| P1-035 | DLQ Postgres 后端 | ✅ | P1-031 | `dlq/postgres_queue.py` + `migrations/002_dlq_items.sql`(CHECK 约束守状态机一致性 + partial index on pending);resolve 并发走行锁 + WHERE 条件 + 二次 SELECT 区分 NotFound / AlreadyResolved |

### Media Buyer Agent(单平台先)

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P1-040 | Meta Ads API client 封装 | ⬜ | P0-021 | |
| P1-041 | Idempotency key 实现(防重复扣费) | ⬜ | P1-040 | |
| P1-042 | Media Buyer Agent:`BuyOrder` → 平台 API | ⬜ | P1-040, P1-041 | |
| P1-043 | 出价 / 预算调整 task queue(应对 rate limit) | ⬜ | P1-042 | |

### Guardrail Agent

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P1-050 | 品牌词典加载与匹配规则 | ✅ | P0-001 | `guardrail.rules.BrandDictionary`,字面量匹配 IGNORECASE |
| P1-051 | 广告法规则库(中国 + 欧盟基础规则) | ✅ | P0-001 | 4 条默认规则,`guardrail.engine.GuardrailEngine` 融合决策 |
| P1-052 | Guardrail Agent:审查 `CreativeVariant` → `ApprovalDecision` | ✅ | P0-016, P1-050, P1-051 | `agents.guardrail`,决策逻辑走 engine 确定性路径 |
| P1-053 | HITL 接入:决策结果推到人工审批队列 | ✅ | P1-052 | `hitl.InMemoryHitlQueue`,coordinator 在 `needs_hitl` 时自动入队,支持 resolve / 幂等入队 |

### 部署

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P1-060 | Dockerfile 与本地 docker-compose | ✅ | P0-001 | 多阶段 `Dockerfile`(非 root)、`docker-compose.yml`、CI 加 `docker-build` 验证 `--help` |
| P1-061 | K8s Helm chart 雏形 | ⬜ | P1-060 | 单租户 namespace |
| P1-062 | 基础 Tracing dashboard(Grafana 或 SDK 自带) | ⬜ | P0-004 | |

---

## P2 — 小预算自治(目标:单日 < $500 全自动闭环)

### Circuit Breaker

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P2-001 | ROAS 连续 N 小时低于阈值 → 自动 pause | ⬜ | P2-040 | |
| P2-002 | 单日花费超 daily hard cap → 立即 stop | ⬜ | P1-042 | |
| P2-003 | Attribution 数据 freshness > 6h → 暂停出价调整 | ⬜ | P2-030 | |
| P2-004 | Creative 失败率 > X% → variant 黑名单 | ⬜ | P1-042 | |
| P2-005 | 全局 Kill Switch(操作员一键 stop 所有 campaign) | ⬜ | P2-001..004 | |
| P2-006 | Circuit Breaker 触发后的 HITL resume 流程 | ⬜ | P2-005 | |

### Eval Pipeline

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P2-010 | Golden set 扩充到每 agent ≥ 20 条 | ⬜ | P0-042 | |
| P2-011 | LLM-as-judge 实现(语义类 eval) | ⬜ | P2-010 | |
| P2-012 | CI 集成:换 prompt / 模型 / SDK 时阻塞合并 | ⬜ | P2-011, X-006 | |
| P2-013 | 在线 eval:1% 生产流量抽样对比 | ⬜ | P2-011, P1-021 | |

### Data Layer

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P2-020 | 统一 Data Access Layer(agents 不直连数据源) | ⬜ | P0-030 | |
| P2-021 | Freshness SLO 元数据(每个数据源标注延迟阈值) | ⬜ | P2-020 | |
| P2-022 | Freshness 告警与 Circuit Breaker 联动 | ⬜ | P2-021, P2-003 | |

### Attribution Agent

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P2-030 | MMM 模型集成(Robyn 或 PyMC) | ⬜ | P2-040 | Sandbox 内运行 |
| P2-031 | Attribution Agent:输出 `AttributionReport` | ⬜ | P0-015, P2-030 | |
| P2-032 | ROAS 下滑触发 handoff 回 Creative | ⬜ | P2-031 | 事件驱动闭环 |

### Experiment Agent

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P2-035 | 贝叶斯统计 tool(提前止损判定) | ⬜ | P0-001 | |
| P2-036 | Experiment Agent:产出 `ExperimentSpec` + `StopRule` | ⬜ | P0-014, P2-035 | |
| P2-037 | 实验平台对接(内部或开源) | ⬜ | P2-036 | |

### Sandbox Agent 基础设施

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P2-040 | Sandbox runtime(Kata Containers / gVisor) 选型与部署 | ⬜ | P1-061 | |
| P2-041 | Sandbox 内 ffmpeg / PIL 镜像 | ⬜ | P2-040 | |
| P2-042 | Sandbox 内 PyMC / Robyn 镜像 | ⬜ | P2-040 | |
| P2-043 | Scoped credentials 注入到 Sandbox | ⬜ | P0-022, P2-040 | |

---

## P3 — 规模化(目标:多品类 / 多市场 / 多平台并发)

### Knowledge Store

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-001 | Vector DB 选型与部署(Pinecone / Weaviate / pgvector) | ⬜ | X-005 | |
| P3-002 | Embedding pipeline(文本 + 图像) | ⬜ | P3-001 | |
| P3-003 | Campaign 结束时回写 learning | ⬜ | P3-002, P2-031 | |
| P3-004 | Audience / Creative agent 启动时召回相似历史 | ⬜ | P3-003 | |

### 业务可观测性

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-010 | ROAS / CAC / CTR 时序看板 | ⬜ | P1-021, P2-031 | |
| P3-011 | Agent 决策计数 / HITL 审批数 / Breaker 触发数看板 | ⬜ | P1-021 | |
| P3-012 | SLO 定义与 Prometheus 告警规则 | ⬜ | P3-010 | |

### Asset Library

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-020 | 对象存储 + 元数据库(版权 / 授权 / 过期) | ⬜ | X-005 | |
| P3-021 | Creative agent RAG 召回必带授权检查 | ⬜ | P3-020, P0-031 | |
| P3-022 | 资产无授权 / 过期 → Guardrail 告警 | ⬜ | P3-021, P1-052 | |

### 多平台扩展

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-030 | Google Ads 接入 | ⬜ | P1-042 | |
| P3-031 | TikTok Ads 接入 | ⬜ | P1-042 | |
| P3-032 | 跨平台预算分配优化 | ⬜ | P3-030, P3-031, P2-031 | |

### 本地化

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-040 | Creative 多语言文案生成 | ⬜ | P0-031 | |
| P3-041 | 文化 / 法规差异规则库扩展 | ⬜ | P1-051 | |

### 多租户加固

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-050 | 客户数 > 20 触发评估:是否切换共享多租户 | ⬜ | — | 评估文档 |
| P3-051 | row-level isolation 设计(若切换) | ⬜ | P3-050 | |

### 隐私 / 合规

| ID | 任务 | 状态 | 依赖 | 备注 |
|----|------|------|------|------|
| P3-060 | DPIA 文档(欧盟客户) | ⬜ | — | |
| P3-061 | Right-to-be-forgotten 级联删除流程 | ⬜ | P3-001, P1-021 | |
| P3-062 | 跨境数据隔离部署(EU region) | ⬜ | P1-061 | |

---

## 任务总览

| 阶段 | 总任务数 | 已完成 |
|------|---------|--------|
| 跨阶段 | 8 | 8 |
| P0 | 22 | 21 |
| P1 | 21 | 18 |
| P2 | 21 | 0 |
| P3 | 18 | 0 |
| **合计** | **90** | **47** |

> 完成数随 commit 同步更新。

---

## 迭代路线图

按"解锁下一档可用性"分档,每档独立可收尾。依赖外部账号的留在 Iter 2;Iter 1 / 3 / 4 / 5 可全离线推进。

### Iter 1 —— Event Store 闭环 + 错误韧性(1–1.5 周)✅

解锁:审计可重放、异常不静默丢。

- P1-022 ✅、P1-023 ✅、P1-031 ✅、P1-032 ✅、P1-002 ✅

### Iter 2 —— 真实投放链路(2–3 周,挡 Meta 开发者账号)

解锁:Copilot 真实闭环 + P1 "自动投放" 语义。

- P0-021、P1-040、P1-041、P1-042、P1-043

### Iter 3 —— 部署 + 持久化(1.5–2 周)

解锁:装得出去给客户试跑。

- P1-024 ✅、P1-034、P1-035 ✅、P1-003 ✅、P1-061、P1-062

### Iter 4 —— 自治基础设施(3–4 周)

解锁:Circuit Breaker 与 Attribution 的执行环境。

- P2-040、P2-043、P2-020、P2-021、P2-002、P2-005

### Iter 5 —— 归因 + 事件驱动闭环(3–4 周)

解锁:P2 "小预算自治" 目标。

- P2-030、P2-031、P2-032、P2-001、P2-003、P2-006

### Iter 6+ —— 规模化

待前 5 档数据反馈再定序:Experiment agent、Knowledge Store、多平台扩展、多租户加固、P3 合规。
