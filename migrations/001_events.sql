-- Event Store 初始 schema(对应 P1-020 / P1-021)。
--
-- 设计要点:
-- - 单 events 表,不按 event_type 切分 —— P1 事件集小,查询走索引即可;拆表反而
--   阻碍跨类型的时序重放。
-- - payload 用 JSONB —— 不同 event_type 的 shape 差异由应用层 schema 约束,DB
--   层不做列约束,换 schema 只发版不迁表。
-- - append-only 通过 BEFORE UPDATE / BEFORE DELETE 触发器兜底 —— 即使误用
--   也会被 DB 拒绝,防止审计链断裂。
-- - 索引覆盖三条高频查询:按 campaign 时序 / 按 correlation 时序 / 按 event_type。
--   写入量远小于查询量,三个复合索引的写放大可接受。
--
-- 兼容性:需要 Postgres 12+(GIN jsonb_path_ops 可选,P1 不启用)。

CREATE TABLE IF NOT EXISTS events (
    event_id        UUID PRIMARY KEY,
    event_type      TEXT NOT NULL,
    source          TEXT NOT NULL,
    schema_version  TEXT NOT NULL DEFAULT 'v1',
    correlation_id  TEXT NOT NULL,
    campaign_id     TEXT,
    payload         JSONB NOT NULL,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 查询模式一:按 campaign 重放(P1-022 CLI 会走这条)。
CREATE INDEX IF NOT EXISTS idx_events_campaign_occurred
    ON events (campaign_id, occurred_at)
    WHERE campaign_id IS NOT NULL;

-- 查询模式二:按 correlation_id 追踪一次 brief 从头到尾的所有事件。
CREATE INDEX IF NOT EXISTS idx_events_correlation_occurred
    ON events (correlation_id, occurred_at);

-- 查询模式三:按 event_type 做看板聚合(P3-011)。
CREATE INDEX IF NOT EXISTS idx_events_type_occurred
    ON events (event_type, occurred_at);

-- append-only 兜底:任何 UPDATE / DELETE 直接报错。
CREATE OR REPLACE FUNCTION events_reject_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only; % denied', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_no_update ON events;
CREATE TRIGGER events_no_update
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION events_reject_mutation();

DROP TRIGGER IF EXISTS events_no_delete ON events;
CREATE TRIGGER events_no_delete
    BEFORE DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_reject_mutation();
