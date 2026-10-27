-- V194: 同一人对同一资源只能有一条 pending 申请（数据库层兜底）
--
-- 为什么需要：`ResourceAccessRequestService.create` 是「先查 pending_for，
-- 没有就插入」的 check-then-insert。两个请求同时到达时都会查到「没有」，
-- 于是插入两条 pending —— 审批列表被同一人同一资源刷屏，批准第一条之后
-- 第二条还挂在待办里，管理员会以为没批成功。
--
-- 为什么不用普通唯一键 (applicant_user_id, resource_type, dataset_id)：
-- 历史申请要保留（approved / rejected / cancelled 可以有多条），唯一性只对
-- pending 成立。MySQL 没有部分索引，所以用「生成列 + 唯一索引」：
-- 非 pending 行该列为 NULL，而唯一索引允许多个 NULL。
--
-- 执行前先收敛存量重复：同一 (申请人, 类型, 资源) 的多条 pending 只保留
-- id 最小的一条，其余置为 cancelled 并写明原因。不先清理，加唯一索引会
-- 直接以 1062 失败并停在半途。

UPDATE `ai_agent_knowledge_access_requests` AS r
JOIN (
    SELECT `applicant_user_id`, `resource_type`, `dataset_id`, MIN(`id`) AS keep_id
    FROM `ai_agent_knowledge_access_requests`
    WHERE `status` = 'pending'
    GROUP BY `applicant_user_id`, `resource_type`, `dataset_id`
    HAVING COUNT(*) > 1
) AS d
    ON  r.`applicant_user_id` = d.`applicant_user_id`
    AND r.`resource_type`   = d.`resource_type`
    AND r.`dataset_id`      = d.`dataset_id`
SET r.`status`         = 'cancelled',
    r.`review_comment` = '系统清理：同一资源存在多条待审批申请，已保留最早一条',
    r.`updated_at`     = NOW()
WHERE r.`status` = 'pending'
  AND r.`id` <> d.`keep_id`;

ALTER TABLE `ai_agent_knowledge_access_requests`
    ADD COLUMN `pending_uniq` VARCHAR(160)
        GENERATED ALWAYS AS (
            CASE
                WHEN `status` = 'pending'
                THEN CONCAT(`applicant_user_id`, '|', `resource_type`, '|', `dataset_id`)
                ELSE NULL
            END
        ) STORED
        COMMENT 'pending 唯一性辅助列：仅 pending 有值，非 pending 为 NULL';

ALTER TABLE `ai_agent_knowledge_access_requests`
    ADD UNIQUE KEY `uk_kbar_pending` (`pending_uniq`);
