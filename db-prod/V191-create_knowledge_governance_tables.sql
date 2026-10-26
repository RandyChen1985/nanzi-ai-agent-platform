-- V191: 知识库治理三张表（访问申请 / 检索日志 / 健康快照）
--
-- 为什么放一个文件：这三张表是同一批治理能力一起上线的，彼此配套——
--   访问申请解决「拿不到权限只能线下找人」，检索日志解决「不知道哪些问题搜不到」，
--   健康快照解决「库一多只能一个个点进去看」。分三个文件只是多跑两次，
--   回滚时还得记住顺序。项目里多表迁移的文件已有先例（V137 七张、V185 五张）。
--
-- 三张表都是新增，不改动任何既有表，对存量数据零影响。

-- ---------------------------------------------------------------------------
-- 1) 访问申请：未授权用户对 public / team 库发起申请，库归属人（或管理员）审批。
--    通过即写一条 ResourcePermission，与手工授权等价，撤回与审计沿用既有链路。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ai_agent_knowledge_access_requests` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `dataset_id` VARCHAR(64) NOT NULL COMMENT '知识库 ID',
    `dataset_name` VARCHAR(255) NULL COMMENT '申请时的库名快照',
    `applicant_user_id` BIGINT NOT NULL COMMENT '申请人用户 ID',
    `applicant_user_name` VARCHAR(64) NULL COMMENT '申请人账号快照',
    `applicant_dept_id` BIGINT NULL COMMENT '申请人部门（审批时判断是否同部门）',
    `reason` VARCHAR(1000) NULL COMMENT '申请理由',
    `status` VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/approved/rejected/cancelled',
    `reviewer_user_id` BIGINT NULL COMMENT '审批人用户 ID',
    `reviewer_user_name` VARCHAR(64) NULL COMMENT '审批人账号快照',
    `review_comment` VARCHAR(1000) NULL COMMENT '审批意见（驳回原因给对方看）',
    `reviewed_at` DATETIME NULL COMMENT '审批时间',
    `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_kbar_dataset_status` (`dataset_id`, `status`),
    KEY `idx_kbar_applicant_status` (`applicant_user_id`, `status`),
    KEY `idx_kbar_status_created` (`status`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库访问申请';

-- ---------------------------------------------------------------------------
-- 2) 检索日志：记录「检索了什么、有没有命中」。
--    与审计日志刻意分表——审计记管理动作（谁对库做了什么）且长期保留；
--    这张表按时间聚合并可定期清理，混在一起会让聚合扫描量爆炸。
--    写入失败静默：检索是主流程，日志写不进去不能反过来让检索报错。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ai_agent_knowledge_retrieval_logs` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `query` VARCHAR(512) NOT NULL COMMENT '检索词（截断到 512 字符）',
    `dataset_ids` JSON NULL COMMENT '本次检索涉及的知识库 ID 列表',
    `hit_count` INT NOT NULL DEFAULT 0 COMMENT '命中切片数，0 即零结果',
    `top_score` DOUBLE NULL COMMENT '最高相似度（用于看"命中了但都很弱"）',
    `latency_ms` INT NULL COMMENT '检索耗时（毫秒）',
    `source` VARCHAR(32) NOT NULL DEFAULT 'test' COMMENT 'test=检索测试 / agent=智能体调用',
    `user_id` BIGINT NULL COMMENT '发起人用户 ID',
    `user_name` VARCHAR(64) NULL COMMENT '发起人账号快照',
    `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP COMMENT '检索时间',
    PRIMARY KEY (`id`),
    KEY `idx_krl_created` (`created_at`),
    KEY `idx_krl_source_created` (`source`, `created_at`),
    KEY `idx_krl_hit_created` (`hit_count`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库检索日志';

-- ---------------------------------------------------------------------------
-- 3) 健康快照：按天巡检落表，而不是打开页面实时算。
--    实时算要对每个库调一次 RAGFlow 文档列表，50 个库就是一串慢请求，
--    而且每次打开都要重算。落表后打开即读，历史趋势也顺带有了。
--    唯一键保证同一天重复巡检是覆盖而不是堆重复行。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ai_agent_knowledge_health_snapshots` (
    `id` BIGINT NOT NULL AUTO_INCREMENT,
    `snapshot_date` DATE NOT NULL COMMENT '巡检日期',
    `dataset_id` VARCHAR(64) NOT NULL COMMENT '知识库 ID',
    `name` VARCHAR(255) NULL COMMENT '巡检时的库名快照',
    `documents` INT NOT NULL DEFAULT 0 COMMENT '文档总数',
    `parsed` INT NOT NULL DEFAULT 0 COMMENT '已解析完成数',
    `parsing` INT NOT NULL DEFAULT 0 COMMENT '解析中',
    `failed` INT NOT NULL DEFAULT 0 COMMENT '解析失败数',
    `unparsed` INT NOT NULL DEFAULT 0 COMMENT '尚未解析',
    `chunks` INT NOT NULL DEFAULT 0 COMMENT '切片总数',
    `is_missing` TINYINT(1) NOT NULL DEFAULT 0 COMMENT '引擎侧已不存在（本地仍有配置）',
    `visibility` VARCHAR(32) NULL COMMENT '可见范围快照',
    `owner_user_id` BIGINT NULL COMMENT '归属人快照',
    `stale_days` INT NULL COMMENT '距最近一次更新的天数',
    `created_at` DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uix_khs_date_dataset` (`snapshot_date`, `dataset_id`),
    KEY `idx_khs_date` (`snapshot_date`),
    KEY `idx_khs_dataset` (`dataset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库健康巡检快照';

-- ---------------------------------------------------------------------------
-- 4) 配额配置项：默认值写在代码里（50 库 / 2000 文档），这里落成可编辑的配置行。
--    没有这行的话系统配置页「知识库」分组里根本看不到它们 —— 默认值不落表，
--    等于写死。填 0 或负数表示不限。
--    ON DUPLICATE 只更新描述与分类，不覆盖 value：升级时不能冲掉管理员改过的值。
-- ---------------------------------------------------------------------------
INSERT INTO `system_configs` (`key`, `value`, `description`, `category`, `is_secret`) VALUES
('knowledge_quota_max_datasets_per_user', '50', '每个用户最多可创建的知识库数量（只统计自己拥有的库，被授权的库不占额度）。填 0 或负数表示不限。默认 50。', 'knowledge', 0),
('knowledge_quota_max_documents_per_dataset', '2000', '单个知识库内最多可上传的文档数量。填 0 或负数表示不限。默认 2000。', 'knowledge', 0)
ON DUPLICATE KEY UPDATE
`description` = VALUES(`description`),
`category` = VALUES(`category`);
