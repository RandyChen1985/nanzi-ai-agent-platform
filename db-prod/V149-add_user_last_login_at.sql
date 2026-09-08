-- V149: 增加用户上次登录时间字段
ALTER TABLE `ai_agent_users` ADD COLUMN `last_login_at` DATETIME NULL DEFAULT NULL COMMENT '上次登录时间' AFTER `password_updated_at`;
