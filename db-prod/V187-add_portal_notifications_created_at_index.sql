-- V187: 站内信保留期清理索引
--
-- 背景：每日 02:00 的 system_audit_log_maintenance 任务会按
--   created_at < now() - INTERVAL 30 DAY
-- 删除 portal_notifications 的过期站内信（保留天数在 scheduler_service 写死）。
--
-- 为什么需要这条索引：
--   建表时只有两条索引，且都以 user_id 作为前导列：
--     idx_portal_notifications_user_created (user_id, created_at)
--     idx_portal_notifications_user_read    (user_id, read_at)
--   MySQL 无法跳过前导列直接用 created_at 做范围扫描，因此清理语句只能全表扫描。
--   补一条 created_at 单列索引后，维护任务只扫描待删区间，且随表增长保持稳定。
--
-- 幂等：原生 DDL，重复执行时 MySQL 返回 1061 Duplicate key name，
--   apply_sql.py 的跳过名单已包含 1061，可重复执行。

ALTER TABLE `portal_notifications`
    ADD INDEX `idx_portal_notifications_created_at` (`created_at`);
