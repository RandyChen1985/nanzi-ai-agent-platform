-- V82 个人 MCP 分享
-- Date: 2026-10-21
-- Purpose:
--   支持把「我的 MCP」分享给指定同事，对方接受后复制一份到他的个人 MCP。
--   分享即打快照（payload 存完整配置 + 工具清单），源服务之后被改/删都不影响已发出的分享。
--
--   刻意不建物理外键：与 skill_shares / ai_agent_user_departments 同一惯例。
--   物理外键会在管理员删除用户时直接撞 RESTRICT 报错，而清理还要连带处理 share 的两端，
--   统一由 management.delete_user 在应用层完成。
--
--   幂等：IF NOT EXISTS，可重复执行。

CREATE TABLE IF NOT EXISTS mcp_server_shares (
    id VARCHAR(36) PRIMARY KEY,
    source_user_id BIGINT NOT NULL,
    source_server_id VARCHAR(36) NOT NULL,
    server_name VARCHAR(100) NOT NULL,
    remark VARCHAR(500),
    sse_url TEXT NOT NULL,
    payload TEXT,
    include_credentials BOOLEAN NOT NULL DEFAULT TRUE,
    target_user_id BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    message VARCHAR(500),
    accepted_server_id VARCHAR(36),
    created_at TIMESTAMP NOT NULL,
    responded_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mcp_shares_source
    ON mcp_server_shares (source_user_id, source_server_id, status);

CREATE INDEX IF NOT EXISTS idx_mcp_shares_target
    ON mcp_server_shares (target_user_id, status, created_at);

COMMENT ON TABLE "mcp_server_shares" IS '个人 MCP 分享记录';
COMMENT ON COLUMN "mcp_server_shares"."id" IS '分享记录 ID（uuid4）';
COMMENT ON COLUMN "mcp_server_shares"."source_user_id" IS '分享人用户 ID';
COMMENT ON COLUMN "mcp_server_shares"."source_server_id" IS '源个人 MCP 服务 ID';
COMMENT ON COLUMN "mcp_server_shares"."server_name" IS '分享瞬间的服务显示名快照';
COMMENT ON COLUMN "mcp_server_shares"."remark" IS '分享瞬间的备注快照';
COMMENT ON COLUMN "mcp_server_shares"."sse_url" IS '分享瞬间的服务地址快照';
COMMENT ON COLUMN "mcp_server_shares"."payload" IS '配置与工具清单的 JSON 快照（敏感字段已是密文）；终态清空';
COMMENT ON COLUMN "mcp_server_shares"."include_credentials" IS '是否携带认证凭证（Token/断言私钥）';
COMMENT ON COLUMN "mcp_server_shares"."target_user_id" IS '接收人用户 ID';
COMMENT ON COLUMN "mcp_server_shares"."status" IS 'PENDING/ACCEPTED/REJECTED/WITHDRAWN';
COMMENT ON COLUMN "mcp_server_shares"."message" IS '附言';
COMMENT ON COLUMN "mcp_server_shares"."accepted_server_id" IS '接收人安装后的 MCP 服务 ID（可能带 -2 后缀）';
COMMENT ON COLUMN "mcp_server_shares"."created_at" IS '创建/最近一次刷新时间';
COMMENT ON COLUMN "mcp_server_shares"."responded_at" IS '接受/拒绝/撤回时间';
