-- V143: 增加 MCP OAuth Client 全员共享标记
ALTER TABLE sys_mcp_oauth_clients
    ADD COLUMN is_shared TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为全员共享 Client：1 共享，0 私有' AFTER scope_version,
    ADD KEY idx_mcp_oauth_client_is_shared (is_shared);
