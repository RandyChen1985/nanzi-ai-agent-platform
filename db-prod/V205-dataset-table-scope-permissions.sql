-- V205: 数据集权限扩展到「表级 / 行级 / 全员」（MySQL）
--
-- 背景：在此之前 `ai_agent_resource_permissions` 只有
-- `resource_type='metadata' + resource_id=<MetaDataset.id>` 一行，
-- 语义是「有这个数据集权限 = 数据集内所有表都可查」，既不能把某几张表收窄，
-- 也无法表达「全员只读公共维表」「某人临时授权到某天」。
--
-- 新增列（NULL 一律表示历史行为，保证老数据/老接口零改动）：
-- - subject_type：user / role / all（全员基线）。NULL 时按 user_id / role_id 推断；
-- - scope：{"mode":"all"|"list","tables":[MetaTable.id...]}。NULL = all（数据集内全部表）；
--   list 为空数组表示一张表都不允许（fail-closed），不要写成 all；
-- - row_policy：inherit / none / custom。NULL = inherit（继续走 meta_datasets.row_filter_config）；
-- - row_condition：row_policy='custom' 时的行级条件，形如 `fact_task.dept_code = 'FIN'`；
-- - expire_at：授权到期时间，NULL = 长期有效；过期行在判定时被忽略但保留可续期。
--
-- 后端不把这些列映射进 ORM（见 app/services/dataset_scope_service.py 的列探测），
-- 因此本迁移执行前代码可以先上线，会整体降级为「数据集级授权」的历史行为。
--
-- 注意：ALTER 不可重入，仅执行一次（与本目录其它版本化迁移一致）。
ALTER TABLE `ai_agent_resource_permissions`
  ADD COLUMN `subject_type` VARCHAR(16) NULL
    COMMENT '授权主体: user/role/all；NULL 按 user_id/role_id 推断' AFTER `role_id`,
  ADD COLUMN `scope` JSON NULL
    COMMENT '表级范围 {"mode":"all"|"list","tables":[meta_tables.id...]}；NULL=全部表' AFTER `resource_id`,
  ADD COLUMN `row_policy` VARCHAR(16) NULL
    COMMENT '行级策略: inherit/none/custom；NULL=inherit' AFTER `scope`,
  ADD COLUMN `row_condition` TEXT NULL
    COMMENT 'row_policy=custom 时的行级条件（表.字段 运算符 值）' AFTER `row_policy`,
  ADD COLUMN `expire_at` DATETIME NULL
    COMMENT '授权到期时间；NULL=长期有效，过期行不参与判定' AFTER `row_condition`;

-- 历史行回填主体类型：只回填有明确主体的行（user_id 优先，其次 role_id）。
--
-- **不要**把 user_id / role_id 都为 NULL 的行回填成 'all'：同一张表里还有
-- `menu:*` / `element:*` 的权限行，它们的 user_id / role_id 也是 NULL，
-- 但语义是「默认可见的菜单/元素」，不是「全员基线」。一旦标成 all，
-- 任何把它们按 subject_type='all' 聚合的代码都会给所有人发这些权限。
-- 这类行保持 NULL，由代码按「无法判定主体 → 不参与判定」处理。
UPDATE `ai_agent_resource_permissions`
SET `subject_type` = CASE
    WHEN `user_id` IS NOT NULL THEN 'user'
    WHEN `role_id` IS NOT NULL THEN 'role'
  END
WHERE `subject_type` IS NULL
  AND (`user_id` IS NOT NULL OR `role_id` IS NOT NULL);

-- 判定时按 (resource_type, resource_id) 一次取该数据集的全部授权行，再按主体分组。
CREATE INDEX `idx_arp_metadata_subject`
  ON `ai_agent_resource_permissions` (`resource_type`, `resource_id`, `subject_type`);
