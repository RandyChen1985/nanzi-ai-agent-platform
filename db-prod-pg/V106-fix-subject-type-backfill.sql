-- V106: 撤销 V105 对「非数据集权限行」的主体类型回填（PostgreSQL）
--
-- 与 db-prod/V206-fix-subject-type-backfill.sql 对应。
--
-- V105 最初把 user_id / role_id 都为 NULL 的历史行回填成了 subject_type='all'。
-- 但 `ai_agent_resource_permissions` 里还存着 `menu:*` / `element:*` 权限行，
-- 它们同样以 user_id / role_id = NULL 表示「默认可见的菜单/元素」，
-- 并不是「全员基线」。被标成 'all' 之后，任何按 subject_type='all' 聚合的代码
-- 都会把 `element:metadata:edit` 这类权限发给所有用户——属于提权。
--
-- 代码侧已经收紧（全员只认 resource_type='metadata' + subject_type='all'），
-- 本迁移把被误标的历史数据恢复成 NULL。
--
-- 幂等：重复执行第二次匹配 0 行。
UPDATE "ai_agent_resource_permissions"
SET "subject_type" = NULL
WHERE "subject_type" = 'all'
  AND "user_id" IS NULL
  AND "role_id" IS NULL
  AND ("resource_type" IS NULL OR "resource_type" <> 'metadata');
