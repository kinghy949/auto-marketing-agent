-- HITL 工单落盘 schema(P1-034)。
--
-- 与 DLQ 同一形态:状态机 pending → approved/rejected/expired;非 append-only,
-- resolve 会 UPDATE。approval / variant 完整 dump 成 JSONB 存 —— 审批者需要
-- 看到"当时的 variant 是什么、Guardrail 给出的决策是什么",之后 variant
-- 再版本化也不影响历史工单。
--
-- brand_guardrails 用 TEXT[] 而非 JSONB:值就是 `tuple[str, ...]`,天然适配
-- PG 的数组类型,也便于按字符串搜索(未来看板用)。
--
-- 兼容性:Postgres 12+。按文件名字典序 init,排在 002_dlq_items.sql 之后。

-- item_id 从 approval_id 派生(`hitl:<approval_id>`),天然做幂等主键。
-- INSERT ON CONFLICT (item_id) DO NOTHING 保证同一 approval 重复入队不破坏既有工单。
CREATE TABLE IF NOT EXISTS hitl_items (
    item_id           TEXT PRIMARY KEY,
    correlation_id    TEXT NOT NULL,
    approval          JSONB NOT NULL,
    variant           JSONB NOT NULL,
    brand_guardrails  TEXT[] NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at        TIMESTAMPTZ,
    reviewer          TEXT,
    reviewer_note     TEXT,
    -- pending 时决策字段必须空,终态必须填齐(reviewer_note 可选,不约束)。
    CHECK (
        (status = 'pending'  AND decided_at IS NULL     AND reviewer IS NULL) OR
        (status <> 'pending' AND decided_at IS NOT NULL AND reviewer IS NOT NULL)
    )
);

-- list_pending 是审批工作台最高频查询。partial 只收 pending。
CREATE INDEX IF NOT EXISTS idx_hitl_pending_created
    ON hitl_items (created_at)
    WHERE status = 'pending';

-- 跨表 join events / dlq_items 时按 correlation_id。
CREATE INDEX IF NOT EXISTS idx_hitl_correlation
    ON hitl_items (correlation_id);
