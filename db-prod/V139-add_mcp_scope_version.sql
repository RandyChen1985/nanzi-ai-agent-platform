-- V139: 记录 Platform MCP Client Scope 版本，判断 Scope 变更后是否已重新签发 Token
-- 说明：历史 Client/Token 从版本 1 开始；每次实际 Scope 变更由应用递增 Client 版本，
-- 新签发的 Access Token 保存对应版本，服务台据此提示当前用户重新生成 Token。

SET @client_scope_version_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'sys_mcp_oauth_clients'
      AND column_name = 'scope_version'
);
SET @sql := IF(
    @client_scope_version_exists = 0,
    'ALTER TABLE sys_mcp_oauth_clients ADD COLUMN scope_version INT NOT NULL DEFAULT 1 COMMENT ''Client Scope 版本，每次 Scope 变更递增'' AFTER allowed_scopes',
    'SELECT 1'
);
PREPARE add_client_scope_version FROM @sql;
EXECUTE add_client_scope_version;
DEALLOCATE PREPARE add_client_scope_version;

SET @token_scope_version_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'sys_mcp_oauth_access_tokens'
      AND column_name = 'scope_version'
);
SET @sql := IF(
    @token_scope_version_exists = 0,
    'ALTER TABLE sys_mcp_oauth_access_tokens ADD COLUMN scope_version INT NOT NULL DEFAULT 1 COMMENT ''签发 Token 时的 Client Scope 版本'' AFTER scopes',
    'SELECT 1'
);
PREPARE add_token_scope_version FROM @sql;
EXECUTE add_token_scope_version;
DEALLOCATE PREPARE add_token_scope_version;
