---
id: ADR-0002
title: Event Store 表结构与写入 SDK
status: Accepted
date: 2026-04-22
---

# ADR-0002 Event Store 表结构与写入 SDK

## 背景

架构 §3.6 要求每个 agent decision 作为 append-only event 落 Event Store,支持重放与审计。P1-020(schema)和 P1-021(写入 SDK)需要把具体实现定下来,为下游 P1-022 重放 CLI、P1-032 DLQ、P3-010/011 业务可观测性看板打好基础。

本 ADR 不覆盖 Postgres 客户端选型(asyncpg / psycopg3 的比较留到真正接后端时再决)、不覆盖事件查询 API(P1-023)。

## 决策

### 1. 单 `events` 表,不按类型拆分

一张表装所有 event_type。原因:
- P1 事件集只有 7 类(cost_guard.authorized / denied / cost_replan.triggered / guardrail.evaluated / creative.rejected / hitl.enqueued / campaign.completed),拆表带来的管理成本 > 查询收益。
- 重放场景是跨类型时序(按 correlation_id 或 campaign_id 拉全部事件),拆表反而要 UNION。
- 换后端引擎(如 ClickHouse)时也保留单表结构,不绑定 Postgres 特性。

### 2. payload 用 `JSONB`,不做列级约束

每个 event_type 的 payload shape 由应用层 `src/auto_marketing_agent/events/` 保证,DB 只存 JSONB。好处:
- 新增 event_type 不需要迁表,发版即生效。
- 查询可用 `payload @> '{"decision":"reject"}'` 等 JSONB 算子,必要时再加 GIN 索引(P3-011 落看板时评估)。

### 3. append-only 通过触发器兜底

即使应用代码 bug 误发 UPDATE / DELETE,DB 层也会 `RAISE EXCEPTION`。这和 Schema Registry 的 `extra="forbid"` 同一个取向:约束写在最后一道防线,不是最前一道。

### 4. 三个复合索引覆盖查询模式

- `(campaign_id, occurred_at)` —— 按 campaign 重放(P1-022 最常用)
- `(correlation_id, occurred_at)` —— 追踪一次 brief 的全部事件
- `(event_type, occurred_at)` —— 按类型聚合(看板)

写放大可接受:P1 单 campaign 约产生 10-15 条事件,单客户每天上限估算 < 100K 写,远低于 Postgres 单表写入上限。

### 5. Python SDK:`EventStore` Protocol + `InMemoryEventStore` 先行

按 HITL 队列同套路线(P1-053):先用 `Protocol` 定义接口,内存实现满足单进程演示与测试,生产后端(`PostgresEventStore`)在 P2 实装后不改上游。coordinator 依赖 Protocol 而非实现。

## 取消的替代方案

**事件溯源框架(eventstoredb / Axon)**:重度依赖,超出 MVP 需求。P1 只需要"写进去,能按 key 查出来",不需要 aggregate / projection / snapshot 抽象。

**每个 agent 一张表**:与"拆类型表"同病,拒绝理由相同。

**所有决策用 Kafka**:Kafka 不是持久化真源,是传输层。真源还是 DB。并且 MVP 阶段单租户部署不值得 Kafka 的运维复杂度。

## 后续

- P1-020/021 落地本 ADR:SQL migration + `events/` Python SDK + coordinator 集成。
- P1-022 重放 CLI 从 `events` 表按 `campaign_id` 拉全量事件,按 `occurred_at` 排序回放。
- P1-023 查询 API:先给 CLI 用,P3 接到 Web UI。
- P2 接 Postgres 后端时,可能需要把 `Event.payload` 从 `dict[str, Any]` 升级为按 event_type 分辨的 TypedDict,让查询代码拿到精确类型。
