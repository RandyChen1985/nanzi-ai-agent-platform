-- V88: 知识库「业务归属部门」改为部门维度（PostgreSQL）
--
-- 与 MySQL V188 对齐：新增 `owner_dept_id` 指向 `ai_agent_departments.id`，
-- 供左侧知识库树做部门分组与「包含下级部门」过滤。
--
-- 明确边界：该列**不是纯展示字段**。public/private 下只影响左侧树分组与搜索；
-- 但 team（部门级）下它是自动只读权限的判定依据（归属部门含下级部门的成员自动可读）。
-- 可写判定仍由 created_by / 管理员决定。
--
-- 存量数据：历史 `owner` 文本保留，`owner_dept_id` 为 NULL 者归入「未关联部门」。
--
-- 幂等：IF NOT EXISTS，可重复执行。

ALTER TABLE knowledge_base_metadata
    ADD COLUMN IF NOT EXISTS owner_dept_id BIGINT NULL;

COMMENT ON COLUMN knowledge_base_metadata.owner_dept_id IS '业务归属部门 ID（仅浏览过滤，不参与权限）';

CREATE INDEX IF NOT EXISTS idx_kb_owner_dept_id
    ON knowledge_base_metadata (owner_dept_id);
