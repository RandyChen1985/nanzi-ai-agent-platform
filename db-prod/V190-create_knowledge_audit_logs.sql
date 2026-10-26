-- V190: 新增知识库业务审计表，让「谁创建/改了什么/传了什么/删了什么」可查
--
-- 为什么不复用 `ai_agent_access_logs`：
--   那张表是**全站 API 访问日志**——每个请求都由中间件写一条，默认只查 90 天，
--   生产上通常还会定期清理。而业务审计要回答「这个库是谁转交出去的」，
--   这类记录必须长期可查，不能跟访问日志一起被清掉。
--   更关键的是：`dataset_id` 现在埋在 `request_params` 的 JSON 文本里，
--   按库查询只能 `LIKE '%"dataset_id": "xxx"%'` 全表扫描，库一多就查不动。
--
-- 序号说明：V189 已被「归属人」迁移占用，本表顺排 V190。
--
-- 语义：一个 dataset 一条记录，批量操作（如批量删库）按库拆条，
--   保证查任意一个库时都能看到它自己经历过什么。库被删除后日志**保留**。

CREATE TABLE IF NOT EXISTS `ai_agent_knowledge_audit_logs` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `dataset_id` VARCHAR(64) NOT NULL COMMENT '知识库（RAGFlow Dataset）ID',
    `action` VARCHAR(64) NOT NULL COMMENT '操作码：datasets:create / documents:upload / datasets:grant ...',
    `user_id` BIGINT NULL COMMENT '操作人用户 ID',
    `user_name` VARCHAR(64) NULL COMMENT '操作人账号快照',
    `status_code` INT NOT NULL DEFAULT 200 COMMENT 'HTTP 状态码（>=400 即失败）',
    `detail` JSON NULL COMMENT '操作细节（文件名/文档数/授权对象/变更前后）',
    `client_ip` VARCHAR(50) NULL COMMENT '来源 IP',
    `error_message` TEXT NULL COMMENT '失败原因',
    `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    PRIMARY KEY (`id`),
    KEY `idx_kal_dataset_id` (`dataset_id`),
    KEY `idx_kal_dataset_created` (`dataset_id`, `created_at`),
    KEY `idx_kal_action` (`action`, `created_at`),
    KEY `idx_kal_user` (`user_name`),
    KEY `idx_kal_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库业务审计日志';
