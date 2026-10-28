-- V197: 会话在线分享（V195/V196 预留给 knowledge-base-hardening 计划）
-- 分享即快照：content_json 冻结创建那一刻的问答正文，之后原会话的任何变化都不影响分享页。
-- 过期不落库（expires_at 为 NULL 表示永久，过期状态由时间计算），避免引入定时任务。
CREATE TABLE IF NOT EXISTS `ai_conversation_shares` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `share_id` VARCHAR(32) NOT NULL COMMENT '对外链接 ID（secrets.token_urlsafe(18)）',
  `conversation_id` VARCHAR(50) DEFAULT NULL COMMENT '被分享会话 ID，仅用于追溯',
  `owner_user_id` VARCHAR(64) NOT NULL COMMENT '分享人用户 ID',
  `owner_username` VARCHAR(64) DEFAULT NULL COMMENT '分享人用户名（展示用）',
  `agent_id` VARCHAR(36) DEFAULT NULL COMMENT '分享时智能体 ID',
  `agent_name` VARCHAR(128) DEFAULT NULL COMMENT '分享时智能体显示名',
  `title` VARCHAR(500) DEFAULT NULL COMMENT '会话标题（首轮提问截断 120 字）',
  `access_mode` VARCHAR(16) NOT NULL DEFAULT 'secret' COMMENT 'public / secret',
  `secret_cipher` VARCHAR(512) DEFAULT NULL COMMENT '访问密码密文（Fernet，可回看）',
  `content_json` LONGTEXT COMMENT '冻结的消息快照 [{role, content, created_at}]',
  `message_count` INT NOT NULL DEFAULT 0 COMMENT '消息条数',
  `turn_count` INT NOT NULL DEFAULT 0 COMMENT '问答轮数',
  `expires_at` DATETIME DEFAULT NULL COMMENT '失效时间，NULL=永久',
  `status` VARCHAR(16) NOT NULL DEFAULT 'active' COMMENT 'active / revoked',
  `revoked_at` DATETIME DEFAULT NULL COMMENT '作废时间',
  `view_count` INT NOT NULL DEFAULT 0 COMMENT '成功打开次数',
  `last_viewed_at` DATETIME DEFAULT NULL COMMENT '最近一次成功打开',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ai_conversation_shares_share_id` (`share_id`),
  KEY `idx_ai_conversation_shares_owner` (`owner_user_id`, `created_at`),
  KEY `idx_ai_conversation_shares_conversation` (`conversation_id`),
  KEY `idx_ai_conversation_shares_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话在线分享（只读快照）';
