-- V105: 数据集权限扩展到「表级 / 行级 / 全员」（PostgreSQL）
--
-- 与 db-prod/V205-dataset-table-scope-permissions.sql 对应，语义逐列一致：
-- - subject_type：user / role / all（全员基线）。NULL 时按 user_id / role_id 推断；
-- - scope：{"mode":"all"|"list","tables":[MetaTable.id...]}。NULL = all（数据集内全部表）；
--   list 为空数组表示一张表都不允许（fail-closed）；
-- - row_policy：inherit / none / custom；NULL = inherit；
-- - row_condition：row_policy='custom' 时的行级条件；
-- - expire_at：授权到期时间，NULL = 长期有效。
--
-- 后端不把这些列映射进 ORM（见 app/services/dataset_scope_service.py 的列探测），
-- 因此迁移执行前代码可以先上线，会降级为数据集级授权。
--
-- 幂等：逐列 IF NOT EXISTS，重复执行不会报「列已存在」。
ALTER TABLE "ai_agent_resource_permissions" ADD COLUMN IF NOT EXISTS "subject_type" VARCHAR(16);
ALTER TABLE "ai_agent_resource_permissions" ADD COLUMN IF NOT EXISTS "scope" JSONB;
ALTER TABLE "ai_agent_resource_permissions" ADD COLUMN IF NOT EXISTS "row_policy" VARCHAR(16);
ALTER TABLE "ai_agent_resource_permissions" ADD COLUMN IF NOT EXISTS "row_condition" TEXT;
ALTER TABLE "ai_agent_resource_permissions" ADD COLUMN IF NOT EXISTS "expire_at" TIMESTAMP NULL;

COMMENT ON COLUMN "ai_agent_resource_permissions"."subject_type" IS
  '授权主体: user/role/all；NULL 按 user_id/role_id 推断';
COMMENT ON COLUMN "ai_agent_resource_permissions"."scope" IS
  '表级范围 {"mode":"all"|"list","tables":[meta_tables.id...]}；NULL=全部表';
COMMENT ON COLUMN "ai_agent_resource_permissions"."row_policy" IS
  '行级策略: inherit/none/custom；NULL=inherit';
COMMENT ON COLUMN "ai_agent_resource_permissions"."row_condition" IS
  'row_policy=custom 时的行级条件（表.字段 运算符 值）';
COMMENT ON COLUMN "ai_agent_resource_permissions"."expire_at" IS
  '授权到期时间；NULL=长期有效，过期行不参与判定';

-- 历史行回填主体类型：只回填有明确主体的行（user_id 优先，其次 role_id）。
--
-- **不要**把 user_id / role_id 都为 NULL 的行回填成 'all'：同一张表里还有
-- `menu:*` / `element:*` 的权限行，它们的 user_id / role_id 也是 NULL，
-- 但语义是「默认可见的菜单/元素」，不是「全员基线」。这类行保持 NULL，
-- 由代码按「无法判定主体 → 不参与判定」处理。
UPDATE "ai_agent_resource_permissions"
SET "subject_type" = CASE
    WHEN "user_id" IS NOT NULL THEN 'user'
    WHEN "role_id" IS NOT NULL THEN 'role'
  END
WHERE "subject_type" IS NULL
  AND ("user_id" IS NOT NULL OR "role_id" IS NOT NULL);

CREATE INDEX "idx_arp_metadata_subject"
  ON "ai_agent_resource_permissions" ("resource_type", "resource_id", "subject_type");
