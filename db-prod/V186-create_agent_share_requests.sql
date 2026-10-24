-- V186: 智能体分享（发送副本邀请，接收方接受后生成自己的副本）
--
-- 定位：分享**不是**授权共用，而是「把当前配置发一份给对方」。
--   所有者把自己的智能体分享给某个人，对方接受后拿到一个**全新的、属于自己的**智能体，
--   需要重新命名并走向导确认配置，复制过程中剔除接收方无权使用的工具/MCP/技能。
--
-- 为什么不做成「授权可见」：
--   授权共用会让两个用户共享同一份线上配置，改配置时谁能改、改了影响谁都会变模糊；
--   而「给你一份副本」边界清楚，源与副本互不影响，也不需要额外的编辑权设计。
--
-- 系统级智能体不提供分享：
--   系统级的可用范围由管理员统一授权治理（下架时要按授权表统计受影响人数、发通知）。
--   如果所有者能自己往外发副本，等于绕开这套治理，且「谁在用生产基线」答不上来。
--   所以 is_system=true 时既隐藏入口，后端也直接拒绝。
--
-- 新增表 ai_agent_share_requests：
--   snapshot       分享那一刻的完整配置快照（JSON）。用快照而不是接受时现读源，
--                  是为了让「源后来被改 / 被删 / 被提升为系统级」都不影响已经发出的分享。
--   status         PENDING / ACCEPTED / REJECTED / EXPIRED / REVOKED / WITHDRAWN
--                  REVOKED 用于「源在对方接受前变成了系统级」——此时该分享作废，
--                  并提示对方改为向管理员申请使用权限。
--   expires_at     默认 7 天，过期由读取时惰性判定，不依赖定时任务。
--
-- 刻意不建物理外键：与 ai_agent_system_requests / skill_publications 同一惯例，
--   避免删除用户或智能体时撞 RESTRICT，清理由应用层负责。
--
-- 幂等：建表与建索引均 IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS `ai_agent_share_requests` (
    `id` VARCHAR(36) NOT NULL COMMENT '分享记录 ID（uuid4）',
    `agent_id` VARCHAR(36) NOT NULL COMMENT '源智能体 ID',
    `agent_name` VARCHAR(255) NULL COMMENT '源智能体物理标识（冗余，源被删后仍可展示）',
    `agent_display_name` VARCHAR(255) NULL COMMENT '源智能体显示名称（冗余）',
    `owner_user_id` BIGINT NULL COMMENT '分享人用户 ID',
    `owner_user_name` VARCHAR(255) NULL COMMENT '分享人用户名',
    `recipient_user_id` BIGINT NOT NULL COMMENT '接收人用户 ID',
    `recipient_user_name` VARCHAR(255) NULL COMMENT '接收人用户名',
    `snapshot` LONGTEXT NULL COMMENT '分享时的完整配置快照（JSON：元数据 + 版本配置）',
    `snapshot_version` INT NOT NULL DEFAULT 1 COMMENT '快照结构版本，便于后续演进',
    `status` VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / ACCEPTED / REJECTED / EXPIRED / REVOKED / WITHDRAWN',
    `share_message` VARCHAR(500) NULL COMMENT '分享留言（可选）',
    `share_reason` VARCHAR(500) NULL COMMENT '状态变更原因（如作废原因）',
    `new_agent_id` VARCHAR(36) NULL COMMENT '接受后生成的副本智能体 ID',
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '分享时间',
    `expires_at` DATETIME NULL COMMENT '过期时间（默认 7 天）',
    `responded_at` DATETIME NULL COMMENT '接受/拒绝/作废时间',
    PRIMARY KEY (`id`),
    KEY `idx_agent_share_recipient` (`recipient_user_id`, `status`, `created_at`),
    KEY `idx_agent_share_agent` (`agent_id`, `status`),
    KEY `idx_agent_share_owner` (`owner_user_id`, `created_at`)
-- 必须显式指定 collation：ai_agents 是老表（utf8mb4_unicode_ci），库默认是
-- utf8mb4_0900_ai_ci，两张表混用会在 agent_id 关联时抛 1267 Illegal mix of collations。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='智能体分享记录（副本邀请）';
