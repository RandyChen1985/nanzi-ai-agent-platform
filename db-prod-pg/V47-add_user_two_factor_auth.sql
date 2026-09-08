-- V47: 为用户表添加 Google 身份验证器两步验证 (2FA / TOTP) 字段
ALTER TABLE "ai_agent_users"
    ADD COLUMN IF NOT EXISTS "two_factor_enabled" BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS "two_factor_secret" VARCHAR(512) NULL DEFAULT NULL;

COMMENT ON COLUMN "ai_agent_users"."two_factor_enabled" IS '是否启用 Google 身份验证器两步验证(2FA)';
COMMENT ON COLUMN "ai_agent_users"."two_factor_secret" IS '两步验证 TOTP 密钥(Base32/加密存储)';
