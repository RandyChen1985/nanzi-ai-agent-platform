-- V87: 站内信保留期清理索引（PostgreSQL）
--
-- 与 MySQL V187 对齐：每日 02:00 的 system_audit_log_maintenance 任务按
--   created_at < now() - INTERVAL '30 days'
-- 删除 portal_notifications 的过期站内信。
--
-- 既有两条索引都以 user_id 为前导列（idx_portal_notifications_user_created 与
-- idx_portal_notifications_user_read），无法单独支撑 created_at 的范围扫描，
-- 清理会退化为全表扫描。补一条 created_at 单列索引让维护任务只扫待删区间。
--
-- 幂等：IF NOT EXISTS，可重复执行。

CREATE INDEX IF NOT EXISTS idx_portal_notifications_created_at
    ON portal_notifications (created_at);
