-- V89: 知识库「归属人」从用户名快照改为用户主键（PostgreSQL）
--
-- 与 MySQL V189 对齐：新增 `owner_user_id` 作为管理权（可读可写可删可授权）的
-- 唯一真源；`created_by` 降级为「创建人」历史快照，仅用于展示，不随转移改写。
--
-- 判定口径：`owner_user_id` 非空只认它；为空时回落 `created_by` 文本比较，
-- 行为与改造前一致（兼容未回填/已离职的老库）。
--
-- 存量回填：按 `user_name` 唯一关联补齐，对不上的保持 NULL，由兜底继续生效；
-- 管理员可用「转移归属」显式指定新归属人。
--
-- 幂等：IF NOT EXISTS；回填只作用于 `owner_user_id IS NULL` 的行。

ALTER TABLE knowledge_base_metadata
    ADD COLUMN IF NOT EXISTS owner_user_id BIGINT NULL;

CREATE INDEX IF NOT EXISTS idx_kb_owner_user_id
    ON knowledge_base_metadata (owner_user_id);

UPDATE knowledge_base_metadata AS kb
SET owner_user_id = u.id
FROM ai_agent_users AS u
WHERE u.user_name = kb.created_by
  AND kb.owner_user_id IS NULL
  AND kb.created_by IS NOT NULL
  AND kb.created_by <> '';

COMMENT ON COLUMN knowledge_base_metadata.owner_user_id IS
    '归属人用户ID（管理权判定依据；为空时回落 created_by 文本）';
