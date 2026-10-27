-- V94: 同一人对同一资源只能有一条 pending 申请（PostgreSQL）
--
-- 与 MySQL V194 对齐。PG 支持部分唯一索引，不需要生成列：
--   CREATE UNIQUE INDEX ... WHERE status = 'pending'
-- 这样 approved / rejected / cancelled 的历史记录可以有多条，
-- 只有「正在等待审批」的同一 (申请人, 类型, 资源) 必须唯一。
--
-- 执行前先收敛存量重复（保留 id 最小的一条），否则建索引会以重复键失败。
-- 原因与影响详见 MySQL V194 注释。

WITH dup AS (
    SELECT applicant_user_id, resource_type, dataset_id, MIN(id) AS keep_id
    FROM ai_agent_knowledge_access_requests
    WHERE status = 'pending'
    GROUP BY applicant_user_id, resource_type, dataset_id
    HAVING COUNT(*) > 1
)
UPDATE ai_agent_knowledge_access_requests AS r
SET status = 'cancelled',
    review_comment = '系统清理：同一资源存在多条待审批申请，已保留最早一条',
    updated_at = CURRENT_TIMESTAMP
FROM dup
WHERE r.status = 'pending'
  AND r.applicant_user_id = dup.applicant_user_id
  AND r.resource_type = dup.resource_type
  AND r.dataset_id = dup.dataset_id
  AND r.id <> dup.keep_id;

CREATE UNIQUE INDEX IF NOT EXISTS uk_kbar_pending
    ON ai_agent_knowledge_access_requests (applicant_user_id, resource_type, dataset_id)
    WHERE status = 'pending';
