-- V192: 资源广场 —— 申请表区分资源类型 + 数据集业务审计
--
-- 两件事：
--
-- 1) `ai_agent_knowledge_access_requests` 原本只服务知识库。资源广场要让数据集
--    也走「申请 → 审批 → 落授权」这条链路。选择加 `resource_type` 而不是再建一张表：
--    统一审批页要把两类申请按时间混排分页，两张表就得 UNION，排序、总数、游标
--    都要各写一遍，后面加第三种资源还得再来一次。
--
--    取值：
--      dataset  = 知识库（RAGFlow Dataset），dataset_id 存 RAGFlow 的 dataset id
--      metadata = 数据集（meta_datasets），dataset_id 存 meta_datasets.id 的字符串
--    存量行全部是知识库，默认值 'dataset' 即可，不需要回填脚本。
--
--    `dataset_name` 继续沿用为「提交时的名称快照」：资源改名或删除后，申请记录
--    仍然显示当时申请的是什么，不会变成一串 ID。
--
-- 2) 资源广场的数据集目录需要一个总开关。开关要出现在「系统配置」页，就必须在
--    system_configs 里有这一行——只加后端校验不插种子，管理员在界面上根本看不到它。
--    默认 true：数据集自助申请是本次能力的核心，默认关等于上线了却看不见；
--    留开关是给「数据集只走管理员手工授权」的团队。
--
-- 3) 数据集侧没有任何长期业务审计。现有 `ai_agent_access_logs` 是全站 API 日志
--    （默认只查 90 天、会定期清理），且 resource_id 埋在 request_params JSON 里，
--    按数据集查只能全表 LIKE。而 `ResourcePermission` 只有当前状态、没有历史，
--    「这个数据集是谁在什么时候授权给谁的」事后查不出来。所以按知识库
--    `ai_agent_knowledge_audit_logs` 的同一套做法，建独立审计表。
--
--    语义：一个数据集一条记录；批量操作按数据集拆条；数据集被删除后日志保留。

ALTER TABLE `ai_agent_knowledge_access_requests`
    ADD COLUMN `resource_type` VARCHAR(20) NOT NULL DEFAULT 'dataset'
    COMMENT '资源类型: dataset=知识库 / metadata=数据集' AFTER `dataset_id`;

ALTER TABLE `ai_agent_knowledge_access_requests`
    ADD INDEX `idx_kbar_type_status` (`resource_type`, `status`);

CREATE TABLE IF NOT EXISTS `ai_agent_metadata_audit_logs` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `metadata_dataset_id` BIGINT NOT NULL COMMENT 'meta_datasets.id',
    `action` VARCHAR(64) NOT NULL COMMENT '操作码: metadata:request_access / metadata:grant / metadata:reject ...',
    `user_id` BIGINT NULL COMMENT '操作人用户 ID',
    `user_name` VARCHAR(64) NULL COMMENT '操作人账号快照',
    `status_code` INT NOT NULL DEFAULT 200 COMMENT 'HTTP 状态码（>=400 即失败）',
    `detail` JSON NULL COMMENT '操作细节（申请ID/目标用户/审批意见/变更前后）',
    `client_ip` VARCHAR(50) NULL COMMENT '来源 IP',
    `error_message` TEXT NULL COMMENT '失败原因',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '操作时间',
    PRIMARY KEY (`id`),
    KEY `idx_mal_dataset_created` (`metadata_dataset_id`, `created_at`),
    KEY `idx_mal_action_created` (`action`, `created_at`),
    KEY `idx_mal_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数据集业务审计日志';

-- 资源广场数据集目录开关（默认开启）
INSERT IGNORE INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
(
  'resource_square_show_datasets',
  'true',
  '「我的工作台 › 资源广场」是否展示数据集目录（默认开启）。关闭后普通用户看不到数据集卡片，需要数据只能由管理员在用户管理中手工授权；已提交的申请不受影响。',
  'general',
  0
);
