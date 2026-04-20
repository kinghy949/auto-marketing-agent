# 架构设计

## 系统总览

```
              ┌─────────────────────────┐
              │   Orchestrator Agent    │  ← 人类只设: KPI / 预算 / 品牌红线
              │   (目标拆解 + 调度)      │
              └───────────┬─────────────┘
                          │ handoff
   ┌──────────────┬───────┼───────┬──────────────┬─────────────┐
   ▼              ▼       ▼       ▼              ▼             ▼
[Audience]   [Creative] [Media] [Experiment] [Attribution] [Guardrail]
 分群 Agent   文案/图    投放    A/B Agent    归因 Agent    合规 Agent
              Agent     Agent
   │            │        │        │             │             │
   ▼            ▼        ▼        ▼             ▼             ▼
 CDP / 数仓   DALL·E /  Meta /   内部实验      MMM / MTA     品牌词典
              Midjourney Google  平台                        + 广告法
              Runway     TikTok
                         Ads API
```

## 各 Agent 职责

| Agent | Tools | 输出 |
|-------|-------|------|
| Orchestrator | 预算分配器、Calendar | 周计划 + 子任务派发 |
| Audience | CDP 上的 SQL、Lookalike API | 受众包 (JSON) |
| Creative | LLM、DALL·E、Runway、品牌素材库 RAG | 文案 + 图/视频变体 ×N |
| Media Buyer | Meta / Google / TikTok Ads MCP | 投放任务 + 出价策略 |
| Experiment | 内部实验平台 API、贝叶斯统计 tool | 实验设计 + 提前止损规则 |
| Attribution | MMM 模型、点击/曝光数据 | ROAS / CAC 报告 |
| Guardrail | 品牌词典、广告法规则库 | 通过/驳回 + 修改建议 |

## 关键设计决策

### Sandbox Agent 用法
- Creative agent 在沙箱中运行 ffmpeg / PIL,处理 5–30 分钟的长任务,不阻塞主流程。
- Attribution agent 在沙箱中运行 MMM 模型(PyMC / Robyn)。

### Sessions 模型
- 每个 Campaign 一个 Session,生命周期约 4–6 周。
- Agent 能够回忆历史洞察(例如"上周 A 文案 CTR 高,本周放大投入")。

### Handoff 拓扑
- **不是线性流水线**,是事件驱动。例如:Attribution 检测到 ROAS 下滑时主动 handoff 给 Creative,触发新一轮迭代。

### Human-in-the-loop 插点
- 单日预算变化 > 30% → 强制人工审批。
- 新品类 / 敏感词触发 → Guardrail 强制人审。
- 其他全自动。

## 真正的难点(别人不会告诉你的)

1. **冷启动数据贫瘠** —— 新品类前 2 周 Attribution agent 没数据可分析,需要先验 prior。
2. **平台 API 限流** —— Meta Ads API 修改出价有 rate limit,需要 task queue。
3. **Creative 同质化** —— LLM 容易陷入相似文案,需要 diversity 奖励信号。
4. **归因黑盒** —— iOS 14 之后 MTA 失真,必须搭 MMM 兜底。
5. **审计与合规** —— 欧盟 DSA、中国广告法,Guardrail 决策必须完整可追溯。

## 参考前作

- **Jasper / Copy.ai** —— 下一代版本正在朝这个方向走。
- **Albert.ai**(被 Zoomd 收购)—— 这个理念的早期版本。
- **Meta Advantage+** —— 平台原生方案,但是黑盒。
- **巨量引擎"妙思"** —— 同方向,中国市场。

## 商业化建议

- **不要做通用平台**,选一个垂直行业:DTC 美妆 / 手游发行 / B2B SaaS。
- 定价:**广告花费的 5–10%**,优于 SaaS 月费 —— 价值锚定明确。
- 起步客户:月广告预算 **$50K – $500K** 的品牌。预算太小不值得自动化,太大有内部团队。
