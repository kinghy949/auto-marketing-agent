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

设计阶段。**尚无运行时代码、依赖清单、构建脚本或测试套件**。仓库目前只有架构文档。**不要凭空编造 build / lint / test 命令** —— 在代码落地之前这些命令都不存在。

第一份代码应建立项目骨架(例如 `pyproject.toml`、包结构、最小 agent runner)。骨架就位后,请同步更新本文件,补上真实命令。

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
