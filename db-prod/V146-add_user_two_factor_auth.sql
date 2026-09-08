-- V146: 为用户表添加 Google 身份验证器两步验证 (2FA / TOTP) 字段
ALTER TABLE ai_agent_users
    ADD COLUMN two_factor_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否启用 Google 身份验证器两步验证(2FA)' AFTER status,
    ADD COLUMN two_factor_secret VARCHAR(512) NULL DEFAULT NULL COMMENT '两步验证 TOTP 密钥(Base32/加密存储)' AFTER two_factor_enabled;
