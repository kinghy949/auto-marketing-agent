# auto-marketing-agent

基于 [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) 构建的**自主营销活动编排系统**。

人类只设定 KPI、预算和品牌红线。一组专业化 agent 协同完成受众分群、创意生成、媒介投放、实验设计、归因分析和合规审查 —— 7 × 24 小时自主运行。

## 当前状态

P0 MVP 骨架已闭环,P1 离线部分(Cost Guard L1 + 重规划 / 重试装饰器 / Guardrail / HITL 队列)已落地。阻塞项仅剩 P0-021(Meta Ads OAuth,需真实开发者账号)。任务追踪见 [`docs/tasks.md`](docs/tasks.md),架构详见 [`docs/architecture.md`](docs/architecture.md)。

## 本地运行

**uv(推荐)**:

```bash
uv sync --all-extras
cp .env.example .env   # 填入 OPENAI_API_KEY
uv run auto-marketing-agent run --brief "美国 18-24 运动人群的 ROAS 活动"
```

**Docker**:

```bash
cp .env.example .env   # 填入 OPENAI_API_KEY
docker compose build
docker compose run --rm app run --brief "美国 18-24 运动人群的 ROAS 活动"
```

## 目标用户

- DTC 电商品牌
- 手游发行商
- B2B SaaS 增长团队

最佳契合区间:**月广告预算 $50K – $500K**。预算太小不值得自动化,太大已有内部团队。

## MVP 路线图

| 阶段 | 范围 | 风险 |
|------|------|------|
| P0 — Copilot | 生成创意 + 推荐受众,投放仍由人完成 | 几乎为零 |
| P1 — 半自动 | 自动投放,所有创意需人审 | 低 |
| P2 — 小预算自治 | 单日 < $500 全自动闭环 | 中 |
| P3 — 规模化 | 多品类、多市场、多平台并发 | 需完整 eval 体系 |

## License

[Apache License 2.0](LICENSE)
