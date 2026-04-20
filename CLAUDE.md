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

以下是**决策**,不是建议。代码落地时应遵守:

- **基于 OpenAI Agents SDK** (`openai-agents`)。多 agent 编排使用该 SDK 的原语(Agents、Sandbox Agents、Tools、Handoffs、Sessions、Tracing),不要自造框架。
- **事件驱动 handoff,而非线性流水线**。Attribution agent 可在 ROAS 下滑时主动 handoff 回 Creative agent。
- **每个 Campaign 一个 Session**,生命周期约 4–6 周。
- **长任务必须放进 Sandbox Agent**(ffmpeg/PIL 媒体处理、MMM 模型拟合),不要在 orchestrator 主循环里跑。
- **Human-in-the-loop 精确插点,不是兜底审批**:仅当单日预算变化 > 30% 或涉及新品类/敏感词时强制人工。不要"为了保险"在其他地方加审批步骤,那会破坏自治目标。
- **MMM 是必备,不是可选**。iOS 14 之后 MTA 不可信,不要把点击归因当作 ground truth 来设计。

## Commit 规范

每个 commit message 末尾必须带尾注(trailer):

```
Co-Authored-By: Claude <noreply@anthropic.com>
```

必须使用此精确格式(不是 harness 默认的 `Claude Opus X.X`)。请用 heredoc 传递 commit message 以保留格式。
