-- V118: 管理员站内信群发的发送记录（PostgreSQL）
--
-- 与 db-prod/V218-create-portal-broadcast-records.sql 同构，两套迁移不可混用。
-- 差异只在类型：JSON -> JSONB、DATETIME -> TIMESTAMP、自增 -> BIGSERIAL。
--
-- 设计取舍见 V218 的注释（只存范围不存名单快照、不建外键、不做保留期清理）。

CREATE TABLE IF NOT EXISTS portal_broadcast_records (
    id BIGSERIAL PRIMARY KEY,
    sender_user_id BIGINT NOT NULL,
    sender_user_name VARCHAR(64) NOT NULL DEFAULT '',
    sender_real_name VARCHAR(64) NULL,
    title VARCHAR(200) NOT NULL,
    content TEXT NOT NULL,
    level VARCHAR(16) NOT NULL DEFAULT 'info',
    audience_user_ids JSONB NULL,
    audience_dept_ids JSONB NULL,
    audience_role_ids JSONB NULL,
    recipient_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_portal_broadcast_records_created
    ON portal_broadcast_records (created_at);

COMMENT ON TABLE portal_broadcast_records IS '管理员站内信群发记录';
COMMENT ON COLUMN portal_broadcast_records.sender_user_id IS '发送者用户ID（写入时刻快照，不建外键）';
COMMENT ON COLUMN portal_broadcast_records.sender_user_name IS '发送者登录名快照';
COMMENT ON COLUMN portal_broadcast_records.sender_real_name IS '发送者姓名快照';
COMMENT ON COLUMN portal_broadcast_records.title IS '消息标题（与 portal_notifications.title 同上限）';
COMMENT ON COLUMN portal_broadcast_records.content IS '消息正文快照（Markdown 原文）';
COMMENT ON COLUMN portal_broadcast_records.level IS '消息级别：info/success/warning/error';
COMMENT ON COLUMN portal_broadcast_records.audience_user_ids IS '指定用户范围（ID 数组）';
COMMENT ON COLUMN portal_broadcast_records.audience_dept_ids IS '部门范围（ID 数组，含下级）';
COMMENT ON COLUMN portal_broadcast_records.audience_role_ids IS '角色范围（ID 数组）';
COMMENT ON COLUMN portal_broadcast_records.recipient_count IS '当次实际写入的站内信条数（去重、仅启用账号）';
COMMENT ON COLUMN portal_broadcast_records.created_at IS '发送时间';
