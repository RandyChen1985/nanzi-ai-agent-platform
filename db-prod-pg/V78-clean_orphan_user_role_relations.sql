-- V78 清理指向已删除用户的角色关系（悬挂行）
-- Date: 2026-10-20
-- Purpose:
--   与 MySQL `db-prod/V177-clean_orphan_user_role_relations.sql` 同义。
--   PostgreSQL 基线里该表带 `ON DELETE CASCADE` 外键，理论上不会产生悬挂行；
--   但若库是由更早的脚本建出、或外键被临时摘除过，仍可能残留。本迁移按同判据兜底清理。
--
-- 安全性：只删除「用户已不存在」的关系行，正常成员不动。幂等，可重复执行。

DELETE FROM ai_agent_user_role_relations r
WHERE NOT EXISTS (
    SELECT 1 FROM ai_agent_users u WHERE u.id = r.user_id
);
