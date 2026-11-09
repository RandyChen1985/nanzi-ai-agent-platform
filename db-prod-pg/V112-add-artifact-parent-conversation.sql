-- V112: 产物支持「父会话归属」（PostgreSQL）。
-- 与 MySQL 侧 db-prod/V212-add-artifact-parent-conversation.sql 保持同一结构：
-- conversation_id 仍是产物实际产生的子会话（审计用），parent_conversation_id /
-- parent_trace_id 让主会话的产物列表与消息角标能聚合到这次委派。
ALTER TABLE "ai_artifacts"
    ADD COLUMN IF NOT EXISTS "parent_conversation_id" VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS "parent_trace_id" VARCHAR(64) NULL;

COMMENT ON COLUMN "ai_artifacts"."parent_conversation_id" IS '产物可见的父会话 ID（子代理委派产物）';
COMMENT ON COLUMN "ai_artifacts"."parent_trace_id" IS '父会话执行 trace_id（子代理委派产物）';

CREATE INDEX IF NOT EXISTS "idx_ai_artifacts_parent_conversation_id"
    ON "ai_artifacts" ("parent_conversation_id");
CREATE INDEX IF NOT EXISTS "idx_ai_artifacts_parent_trace_id"
    ON "ai_artifacts" ("parent_trace_id");
