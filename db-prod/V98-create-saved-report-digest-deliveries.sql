CREATE TABLE IF NOT EXISTS `portal_saved_report_digest_deliveries` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '推送审计主键ID',
  `run_id` BIGINT NOT NULL COMMENT '关联的报表运行历史ID',
  `subscription_id` BIGINT NOT NULL COMMENT '关联的报表订阅ID',
  `channel` VARCHAR(32) NOT NULL COMMENT '推送渠道: inbox, dingtalk, wechat_work, email',
  `digest_payload` JSON NULL COMMENT '本次结构化智能简报JSON',
  `title` VARCHAR(200) NOT NULL COMMENT '实际发送的消息标题',
  `content` TEXT NOT NULL COMMENT '实际发送的消息正文',
  `status` VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '发送状态: pending, success, failed',
  `error_message` TEXT NULL COMMENT '渠道发送失败原因',
  `ai_status` VARCHAR(16) NOT NULL DEFAULT 'disabled' COMMENT 'AI生成状态: success, fallback, disabled',
  `sent_at` DATETIME NULL COMMENT '发送完成时间',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '审计记录创建时间',
  PRIMARY KEY (`id`),
  KEY `idx_saved_report_digest_run` (`run_id`),
  KEY `idx_saved_report_digest_subscription` (`subscription_id`, `created_at`),
  CONSTRAINT `fk_saved_report_digest_run` FOREIGN KEY (`run_id`) REFERENCES `portal_saved_report_runs` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_saved_report_digest_subscription` FOREIGN KEY (`subscription_id`) REFERENCES `portal_saved_report_subscriptions` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='黄金报表智能简报推送审计表';
