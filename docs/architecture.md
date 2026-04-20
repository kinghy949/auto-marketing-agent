# 架构设计 (v2)

> **变更说明:** 相比 v1,本版补齐了 v1 review 暴露的 12 项遗漏:Eval 体系、成本控制、Secrets 管理、错误处理与重试、Circuit Breaker、数据层、跨 Campaign 长期记忆、Agent 契约 schema、Event Store、业务可观测性、资产/品牌库、多租户与部署模型。新增"平台服务层"作为一等公民。

## 1. 系统总览

```
       ┌────────────────── 人类操作员 ──────────────────┐
       │ 输入: KPI / 预算 / 品牌红线                     │
       │ 紧急: Kill Switch / HITL 审批                  │
       └───────────────────────┬────────────────────────┘
                               ▼
                ┌──────────────────────────────┐
                │     Orchestrator Agent       │
                │  目标拆解 / 调度 / 重试 / DLQ │
                └──┬────────────────────────┬──┘
                   │   事件驱动 handoff      │
   ┌───────────────┼─────────┬──────────┬──┴──────────┬──────────┐
   ▼               ▼         ▼          ▼             ▼          ▼
[Audience] [Creative] [Media Buyer] [Experiment] [Attribution] [Guardrail]
   │           │           │            │              │           │
   └───────────┴───────────┼────────────┴──────────────┴───────────┘
                           │ 所有 agent 共享
                           ▼
   ┌──────────────── 平台服务层 (Platform Services) ────────────────┐
   │  Eval Pipeline    │  Cost Guard       │  Circuit Breaker      │
   │  Knowledge Store  │  Secrets Manager  │  Schema Registry      │
   │  Event Store      │  Asset Library    │  Observability        │
   │  Data Layer (CDP / 数仓 / 实时流 / freshness SLO)              │
   └────────────────────────────────┬───────────────────────────────┘
                                    ▼
   ┌──────────────────── 外部系统 ────────────────────┐
   │ Meta / Google / TikTok Ads  │  CDP / Snowflake  │
   │ DALL·E / Runway / MCP        │  内部实验平台     │
   └──────────────────────────────────────────────────┘
```

## 2. 业务 Agent

| Agent | 职责 | Tools | 输出 schema |
|-------|------|-------|------------|
| Orchestrator | 目标拆解、调度、重试、DLQ 处理 | Cost Guard、Calendar、预算分配器 | `CampaignPlan` |
| Audience | 受众分群、Lookalike 扩展 | CDP SQL、Lookalike API、Knowledge 查询 | `AudienceSegment[]` |
| Creative | 文案/图/视频生成 | LLM、DALL·E、Runway、Asset Library RAG | `CreativeVariant[]` |
| Media Buyer | 投放执行、出价策略 | Meta / Google / TikTok Ads MCP | `BuyOrder[]` |
| Experiment | A/B 设计、贝叶斯停止 | 实验平台 API、统计 tool | `ExperimentSpec` + `StopRule` |
| Attribution | ROAS / CAC 计算 | MMM (PyMC/Robyn)、点击/曝光数据、freshness check | `AttributionReport` |
| Guardrail | 品牌/法规审查 | 品牌词典、广告法规则库、Eval golden case | `ApprovalDecision` + 修改建议 |

所有 schema 由 **Schema Registry** 集中管理,Pydantic 强类型,变更走版本号。Handoff payload 必须能被下游 agent 校验通过,否则进 DLQ。

## 3. 平台服务层(新增,一等公民)

### 3.1 Eval Pipeline
- **Golden Set**:每个 agent 有 ≥ 20 个手工标注的 input/expected 对
- **回归测试**:换 prompt / 换模型 / 换 SDK 版本时自动跑
- **在线 eval**:抽样 1% 生产流量做对比评估,异常告警
- **判定者**:Guardrail agent 兼任(rule-based)+ LLM-as-judge(语义类)

### 3.2 Cost Guard
- **三层预算上限**:
  - L1 (软):单次 agent 调用 token ceiling,超过自动 truncate context
  - L2 (中):单 Campaign 单日 token cost cap
  - L3 (硬):平台层 daily LLM spend cap,触顶熔断所有 agent
- 每次 LLM 调用前检查,超限直接拒绝,记入 Event Store

### 3.3 Circuit Breaker(独立于 HITL)
HITL 是**事前审批**,Circuit Breaker 是**事后熔断**:

| 触发条件 | 动作 |
|----------|------|
| 单 Campaign ROAS 连续 N 小时 < 阈值 | 自动 pause + 通知 owner |
| 单日花费 > daily hard cap | 立即 stop,需人工 resume |
| Attribution 数据 freshness 滞后 > 6h | 暂停 Media Buyer 出价调整 |
| 同一 Creative 失败率 > X% | 该 variant 加入黑名单 |
| 全局 kill switch | 一键 stop 所有 campaign(操作员可触发) |

### 3.4 Knowledge Store(跨 Campaign 长期记忆)
- Vector DB(Pinecone / Weaviate / pgvector)
- 存什么:历史 winning creatives、季节性洞察、客群标签语料、过往 incident 复盘
- 谁写:Attribution agent 在 campaign 结束时回写 learning
- 谁读:Audience / Creative agent 启动时召回相似历史

### 3.5 Secrets Manager
- Vault / AWS Secrets Manager / Doppler 三选一
- **Scoped credentials**:Sandbox Agent 只能拿到只读子集 token
- OAuth refresh token 自动轮换
- 审计日志(谁、何时、读了哪个 secret)

### 3.6 Schema Registry
- Pydantic models 集中定义,版本号 (`v1.audience_segment.json`)
- Handoff payload 强类型,反序列化失败 → DLQ
- Schema 变更走 PR review,向后兼容性必检

### 3.7 Event Store(状态持久化)
- 每个 agent decision 写一条 event(append-only)
- 支持 **重放**:给定 campaign_id,可重建任意时间点的状态
- 后端:PostgreSQL + 时序索引,或专门 event store(EventStoreDB)
- 用途:审计 / 回滚 / 离线 eval / 调试

### 3.8 Asset Library
- S3 / 对象存储 + 元数据库(版权、授权范围、过期时间、品牌版本号)
- Creative agent 通过 RAG 召回时,**强制带授权检查**
- 资产无授权或过期 → Creative agent 拒绝使用,触发 Guardrail 告警

### 3.9 Observability
分两层:
- **Tracing**(已有,SDK 自带):agent 调用链路、token 用量、延迟
- **业务指标**(新增):
  - ROAS / CAC / CTR 时序看板
  - 每日 agent 决策数 / HITL 审批数 / Circuit Breaker 触发数
  - SLO:agent 可用率 ≥ 99%、attribution 数据延迟 < 6h、ROAS 告警 < 5min

### 3.10 Data Layer
- **数据源**:CDP(Segment / mParticle)+ 数仓(Snowflake / BigQuery)+ 实时流(Kafka)
- **Freshness SLO**:转化数据 < 1h、花费数据 < 30min、归因数据 < 6h
- **freshness 不达标**:Attribution agent 主动降级或拒绝出报告,Circuit Breaker 联动
- 所有 agent 通过统一 **Data Access Layer** 读取,不直连数据源

## 4. 错误处理与重试

| 失败类型 | 策略 |
|----------|------|
| 平台 API 5xx | 指数退避重试,最多 3 次,失败入 DLQ |
| Sandbox Agent 崩溃 | 自动重启,带相同输入重试 1 次,失败入 DLQ |
| Schema 校验失败 | 不重试,入 DLQ + 告警 Schema Registry owner |
| LLM rate limit | 排队 + 退避 |
| Cost Guard 拒绝 | 不重试,通知 Orchestrator 重新规划 |
| **Idempotency** | 所有 Media Buyer 操作必须带 idempotency key,防止重试导致重复扣费 |

## 5. 关键设计决策

### 5.1 Sandbox Agent 用法
- Creative agent 在沙箱中运行 ffmpeg / PIL,处理 5–30 分钟的长任务,不阻塞主流程
- Attribution agent 在沙箱中运行 MMM 模型(PyMC / Robyn)
- Sandbox 内只能拿到 scoped credentials,不能访问全量 secrets

### 5.2 Sessions vs Knowledge Store
- **Session(短期)**:每个 Campaign 一个,4–6 周生命周期,记 in-campaign 上下文
- **Knowledge Store(长期)**:跨 campaign,记可复用洞察

### 5.3 Handoff 拓扑
- 事件驱动,**不是**线性流水线
- 例:Attribution 检测到 ROAS 下滑 → 主动 handoff 回 Creative
- Handoff payload 强类型 + idempotency key

### 5.4 Human-in-the-loop 插点(精确,不是兜底)
- 单日预算变化 > 30%
- 新品类 / 敏感词
- Circuit Breaker 触发后的 resume

## 6. 多租户与部署

### 6.1 多租户模型(MVP 阶段决策:**单租户独立部署**)
- 每客户独立 K8s namespace + 独立数据库 + 独立 Vault path
- 优点:数据/凭证天然隔离,合规简单
- 缺点:运维成本高
- **何时切换**:客户数 > 20 后再考虑共享基础设施 + row-level isolation

### 6.2 部署拓扑
- K8s + Helm chart
- Sandbox Agent 用 Kata Containers / gVisor 强隔离
- 数据库 / 对象存储 / Vector DB 用云托管(降低运维)
- CI/CD 含 Eval Pipeline 阻塞合并

## 7. 隐私与合规

- **PII 最小化**:Audience agent 只接触 hashed user IDs,不接触原始 PII
- **GDPR**:DPIA 必做,支持 right-to-be-forgotten(级联删除 CDP / Knowledge Store)
- **DSA / 广告法**:Guardrail 决策全量进 Event Store,可追溯每条投放
- **跨境数据**:欧盟客户数据不出区(部署到 EU region)

## 8. 真正的难点(更新版)

1. **冷启动数据贫瘠** —— 新品类前 2 周 Attribution 无数据,Knowledge Store 兜底先验
2. **平台 API 限流** —— Meta Ads 改价有 rate limit,task queue 必备
3. **Creative 同质化** —— LLM 收敛到相似文案,需要 diversity reward
4. **归因黑盒** —— iOS 14 后 MTA 失真,MMM 必备
5. **审计与合规** —— Event Store 全量记录,Guardrail 可追溯
6. **(新)agent 静默退化** —— 没 Eval Pipeline 之前换模型 = 赌博
7. **(新)成本失控** —— 一个 handoff 死循环可一夜烧 $1000+,Cost Guard 必备
8. **(新)数据时效与决策时效错配** —— Attribution 拿 6h 前数据做实时决策 = 反向优化
9. **(新)凭证泄露面** —— Sandbox 拿 full-access token 是定时炸弹,必须 scoped
10. **(新)多 Campaign 资源争抢** —— 共享平台层 API 配额,需要全局调度

## 9. P0 → P3 路线图与平台层依赖

| 阶段 | 必须就位的平台层组件 |
|------|-------------------|
| P0 — Copilot | Schema Registry、Secrets Manager、基础 Tracing |
| P1 — 半自动 | + Cost Guard、Event Store、错误处理与 DLQ |
| P2 — 小预算自治 | + Circuit Breaker、Eval Pipeline、Data Layer freshness SLO |
| P3 — 规模化 | + Knowledge Store、业务指标 SLO、多租户隔离、Asset Library 授权追踪 |

## 10. 参考前作

- **Jasper / Copy.ai** —— 下一代正在朝这个方向走
- **Albert.ai**(被 Zoomd 收购)—— 早期版本
- **Meta Advantage+** —— 平台原生但黑盒
- **巨量引擎"妙思"** —— 同方向,中国市场

## 11. 商业化建议

- **不要做通用平台**,选垂直:DTC 美妆 / 手游发行 / B2B SaaS
- 定价:广告花费的 **5–10%**,价值锚定明确
- 起步客户:月预算 **$50K – $500K**
- **平台层是护城河**:Eval、Knowledge、Circuit Breaker 这三件事做扎实,客户离开成本极高
