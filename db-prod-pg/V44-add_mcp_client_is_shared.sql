-- V44: 增加 MCP OAuth Client 全员共享标记
ALTER TABLE sys_mcp_oauth_clients
    ADD COLUMN IF NOT EXISTS is_shared BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_client_is_shared ON sys_mcp_oauth_clients (is_shared);
COMMENT ON COLUMN sys_mcp_oauth_clients.is_shared IS '是否为全员共享 Client：TRUE 共享，FALSE 私有';
