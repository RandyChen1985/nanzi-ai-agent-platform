-- V40: 记录 Platform MCP Client Scope 版本，判断 Scope 变更后是否已重新签发 Token
-- 说明：历史 Client/Token 从版本 1 开始；每次实际 Scope 变更由应用递增 Client 版本，
-- 新签发的 Access Token 保存对应版本，服务台据此提示当前用户重新生成 Token。

ALTER TABLE "sys_mcp_oauth_clients"
    ADD COLUMN IF NOT EXISTS "scope_version" INTEGER NOT NULL DEFAULT 1;
COMMENT ON COLUMN "sys_mcp_oauth_clients"."scope_version"
    IS 'Client Scope 版本，每次 Scope 变更递增';

ALTER TABLE "sys_mcp_oauth_access_tokens"
    ADD COLUMN IF NOT EXISTS "scope_version" INTEGER NOT NULL DEFAULT 1;
COMMENT ON COLUMN "sys_mcp_oauth_access_tokens"."scope_version"
    IS '签发 Token 时的 Client Scope 版本';
