-- V189: 知识库「归属人」从用户名快照改为用户主键，支持离职/调岗交接
--
-- 背景：`created_by` 是 VARCHAR(64) 的用户名快照，而权限判定拿它和当前登录人的
--   user_name 做字符串比较。一旦用户改名、账号重建或离职，这个名字就对不上任何人，
--   他名下的 private 库会变成「只有管理员能看见」的无主库，team 库的管理权也一并
--   消失；此前也没有任何「把库交给别人」的入口。
--
-- 本次改动：
--   新增 `owner_user_id` 指向 `ai_agent_users.id`，作为**管理权（可读可写可删可授权）
--   的唯一真源**。`created_by` 降级为「创建人」历史快照，仅用于展示，不再随转移改写。
--
-- 判定口径（见 PermissionService.resolve_knowledge_dataset_access）：
--   `owner_user_id` 非空 → 只认它，`created_by` 文本不参与；
--   `owner_user_id` 为空 → 回落 `created_by` 文本比较（兼容未回填/已离职的老库，
--   行为与改造前完全一致，不会因为上线迁移把谁的权限收紧）。
--
-- 存量回填：
--   按 `user_name` 唯一关联补齐。对不上（改名、离职、脏数据）的保持 NULL，
--   由上面那条兜底继续生效；管理员可用「转移归属」显式指定新归属人。
--
-- 幂等：apply_sql.py 会跳过 1060（列已存在）/ 1061（索引已存在）；
--   回填 UPDATE 只作用于 `owner_user_id IS NULL` 的行，重复执行不会覆盖已转移的归属。

ALTER TABLE `knowledge_base_metadata`
    ADD COLUMN `owner_user_id` BIGINT NULL COMMENT '归属人用户ID（管理权判定依据；为空时回落 created_by 文本）' AFTER `owner_dept_id`;

ALTER TABLE `knowledge_base_metadata`
    ADD INDEX `idx_kb_owner_user_id` (`owner_user_id`);

UPDATE `knowledge_base_metadata` AS kb
JOIN `ai_agent_users` AS u ON u.`user_name` = kb.`created_by`
SET kb.`owner_user_id` = u.`id`
WHERE kb.`owner_user_id` IS NULL
  AND kb.`created_by` IS NOT NULL
  AND kb.`created_by` <> '';
