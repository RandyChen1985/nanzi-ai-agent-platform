-- V33: 增加 Agent 版本级工具调用超时配置（PostgreSQL）
ALTER TABLE "ai_agent_versions"
    ADD COLUMN IF NOT EXISTS "toolcall_timeout_seconds" INTEGER NULL;

COMMENT ON COLUMN "ai_agent_versions"."toolcall_timeout_seconds"
    IS '智能体版本级工具调用超时时间（秒），NULL 表示跟随全局，范围 1-86400';
