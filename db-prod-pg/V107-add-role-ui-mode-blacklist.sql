-- V107: 角色级「界面模式黑名单」（PostgreSQL）
--
-- 与 MySQL db-prod/V207-add-role-ui-mode-blacklist.sql 保持同一口径：
--   ''        两种模式都可用（默认）
--   'pro'     禁用专业模式 → 只能用用户模式
--   'user'    禁用用户模式 → 只能用专业模式
--   'user,pro' 非法：必须二选一，写入层直接拒绝
--
-- IF NOT EXISTS 保证重复执行不报错；列不在 ORM 映射里声明，读写走原生 SQL +
-- 列探测（代码可先于迁移上线）。
ALTER TABLE "ai_agent_roles"
    ADD COLUMN IF NOT EXISTS "blocked_ui_modes" VARCHAR(32) NOT NULL DEFAULT '';

COMMENT ON COLUMN "ai_agent_roles"."blocked_ui_modes"
    IS '禁用的界面模式（黑名单，逗号分隔：user/pro；空串=都不禁）';
