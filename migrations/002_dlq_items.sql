-- DLQ 落盘 schema(P1-035)。
--
-- 与 events 表不同:DLQ 不是 append-only,pending → replayed/discarded 的状态
-- 迁移是正常用法。但不可变字段(reason / source / correlation_id / raw_payload /
-- created_at)靠应用层状态机保证,DB 不加 reject-mutation 触发器 —— 运维偶尔
-- 需要修正 typo 或归档数据,触发器会挡住 legit 维护;append-only 触发器只在
-- Event Store 这种"审计主链"场景值得付代价。
--
-- 兼容性:Postgres 12+(与 events 表同)。首次 init 时 docker-entrypoint-initdb.d
-- 按文件名字典序执行,排在 001_events.sql 之后。

-- item_id 同 events 表,用 TEXT 存 `dlq:<uuid4>` 前缀字符串,方便跨表审计一眼看出
-- 归属(events.event_id 是 `evt:...`、HITL.item_id 是 `hitl:...`)。
CREATE TABLE IF NOT EXISTS dlq_items (
    item_id         TEXT PRIMARY KEY,
    reason          TEXT NOT NULL,
    source          TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    campaign_id     TEXT,
    raw_payload     TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'replayed', 'discarded')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    resolver        TEXT,
    resolver_note   TEXT,
    -- 状态机一致性:pending 时 resolved_* 必须为空,终态必须填齐 resolver / resolved_at。
    -- resolver_note 可选,因此不在 CHECK 里强约束。
    CHECK (
        (status = 'pending'  AND resolved_at IS NULL     AND resolver IS NULL) OR
        (status <> 'pending' AND resolved_at IS NOT NULL AND resolver IS NOT NULL)
    )
);

-- list_pending 是运维看板最高频查询。partial index 只收 pending 行,
-- 已 resolve 的历史不进索引,显著缩小热路径 B-tree。
CREATE INDEX IF NOT EXISTS idx_dlq_pending_created
    ON dlq_items (created_at)
    WHERE status = 'pending';

-- list_by_reason 可能查历史,不做 partial。
CREATE INDEX IF NOT EXISTS idx_dlq_reason_created
    ON dlq_items (reason, created_at);

-- 跨表按 correlation_id 关联 events —— triage 时先拉全链路,再决定处置。
CREATE INDEX IF NOT EXISTS idx_dlq_correlation
    ON dlq_items (correlation_id);
