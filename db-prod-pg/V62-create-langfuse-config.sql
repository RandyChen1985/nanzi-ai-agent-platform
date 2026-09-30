-- =====================================================================
-- 变更原因：新增 Langfuse LLM 链路追踪的独立配置表，并注册独立保存权限。
-- 需求背景：LLM 可观测性接入（排障与性能链路追踪），配置与 system_configs 解耦，
--           使用单行强类型配置表，密钥加密存储。
-- 创建时间：2026-09-30
-- 创建人：Antigravity Agent
-- 注意事项：该脚本仅做生成，不得由 Agent 自动执行，由用户手动导入更新到数据库。
-- 说明：字段与取值校验由应用层 app/services/ai/observability/config_store.py 负责；
--       本脚本只建表、灌入默认行（开关默认关闭）并注册权限元素。
-- =====================================================================

CREATE TABLE IF NOT EXISTS "langfuse_config" (
    -- 表说明：Langfuse LLM 链路追踪配置（单行）
    -- 固定为 1 的单行配置主键
    "id" INTEGER NOT NULL DEFAULT 1,
    -- 总开关：关闭时连 span 都不创建
    "enabled" BOOLEAN NOT NULL DEFAULT FALSE,
    -- Langfuse 地址，如 http://langfuse:3000
    "host" VARCHAR(512),
    -- Langfuse public key（非机密，明文存储）
    "public_key" VARCHAR(255),
    -- Langfuse secret key（Fernet 密文，前缀 langfusekey:v1:）
    "secret_key" TEXT,
    -- trace 粒度采样率 0.000~1.000
    "sample_rate" NUMERIC(4,3) NOT NULL DEFAULT 1.000,
    -- 是否上报提示词/补全内容
    "capture_content" BOOLEAN NOT NULL DEFAULT TRUE,
    -- Langfuse environment，留空不写入该属性
    "environment" VARCHAR(64),
    -- 发布版本，便于按版本对比
    "release" VARCHAR(64),
    -- 导出/探测超时秒数
    "timeout_seconds" INTEGER NOT NULL DEFAULT 5,
    -- 深链模板，含 {trace_id} 占位
    "trace_url_template" VARCHAR(512),
    -- 最后修改人
    "updated_by" VARCHAR(64),
    -- 创建时间
    "created_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- 更新时间
    "updated_at" TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY ("id")
);

-- 默认行：开关关闭、内容采集开启、采样 100%、超时 5s
INSERT INTO "langfuse_config"
    ("id", "enabled", "sample_rate", "capture_content", "timeout_seconds", "created_at", "updated_at")
SELECT 1, FALSE, 1.000, TRUE, 5, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM "langfuse_config" WHERE "id" = 1
);

-- 独立功能权限：Langfuse 配置的保存与连通性探测
INSERT INTO "ai_agent_resource_permissions"
    ("resource_type", "resource_id", "enabled", "created_at", "updated_at")
SELECT 'element', 'element:system:langfuse_save', TRUE, NOW(), NOW()
WHERE NOT EXISTS (
    SELECT 1
    FROM "ai_agent_resource_permissions"
    WHERE "resource_type" = 'element'
      AND "resource_id" = 'element:system:langfuse_save'
);
