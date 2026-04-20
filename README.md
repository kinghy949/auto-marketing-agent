# auto-marketing-agent

基于 [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) 构建的**自主营销活动编排系统**。

人类只设定 KPI、预算和品牌红线。一组专业化 agent 协同完成受众分群、创意生成、媒介投放、实验设计、归因分析和合规审查 —— 7 × 24 小时自主运行。

## 当前状态

早期设计阶段。架构详见 [`docs/architecture.md`](docs/architecture.md)。**尚无运行时代码**。

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

待定
