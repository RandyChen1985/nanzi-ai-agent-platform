-- V64: 用户资料增加邮箱字段（对应 MySQL V163）
-- PG 原生支持 IF NOT EXISTS，因此无需依赖错误码吞掉，真正幂等。

ALTER TABLE "ai_agent_users"
    ADD COLUMN IF NOT EXISTS "email" VARCHAR(254) NULL;

COMMENT ON COLUMN "ai_agent_users"."email" IS '邮箱（小写归一化存储，全局唯一；NULL=未填写）';

CREATE UNIQUE INDEX IF NOT EXISTS "uk_ai_agent_users_email"
    ON "ai_agent_users" ("email");
