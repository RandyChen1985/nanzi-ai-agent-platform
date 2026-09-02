-- V38: NanZi Platform MCP 入站 OAuth2 Client、Token 与审计表
-- 说明：这是 NanZi 作为 MCP Server 对外提供服务的入站认证数据；不修改出站 MCP 表。

CREATE TABLE IF NOT EXISTS sys_mcp_platform_config (
    id SMALLINT PRIMARY KEY,
    platform_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    agent_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    conversation_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    knowledge_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_by VARCHAR(64),
    updated_by VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE sys_mcp_platform_config IS 'NanZi Platform MCP 服务专属配置';
COMMENT ON COLUMN sys_mcp_platform_config.id IS '单例配置 ID，固定为 1';
COMMENT ON COLUMN sys_mcp_platform_config.platform_enabled IS 'Platform MCP 总开关';
COMMENT ON COLUMN sys_mcp_platform_config.agent_enabled IS '智能体能力组开关';
COMMENT ON COLUMN sys_mcp_platform_config.conversation_enabled IS '会话能力组开关';
COMMENT ON COLUMN sys_mcp_platform_config.knowledge_enabled IS '知识库能力组开关';
COMMENT ON COLUMN sys_mcp_platform_config.metadata_enabled IS '元数据能力组开关';
COMMENT ON COLUMN sys_mcp_platform_config.created_by IS '首次创建人用户 ID';
COMMENT ON COLUMN sys_mcp_platform_config.updated_by IS '最后修改人用户 ID';
COMMENT ON COLUMN sys_mcp_platform_config.created_at IS '创建时间';
COMMENT ON COLUMN sys_mcp_platform_config.updated_at IS '最后更新时间';

INSERT INTO sys_mcp_platform_config (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_clients (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(128) NOT NULL UNIQUE,
    client_name VARCHAR(200) NOT NULL,
    client_type VARCHAR(20) NOT NULL DEFAULT 'confidential',
    client_secret_hash VARCHAR(128),
    redirect_uris JSONB NOT NULL,
    allowed_grant_types JSONB NOT NULL,
    allowed_scopes JSONB NOT NULL,
    allowed_agent_ids JSONB,
    allowed_knowledge_base_ids JSONB,
    allowed_metadata_dataset_ids JSONB,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    created_by VARCHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    disabled_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_client_status ON sys_mcp_oauth_clients(status);

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_grants (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    scopes JSONB NOT NULL,
    resource VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    consented_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    revoked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uix_mcp_oauth_grant_subject UNIQUE (client_id, user_id, resource)
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_grant_user ON sys_mcp_oauth_grants(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_grant_status ON sys_mcp_oauth_grants(status);

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_authorization_codes (
    id VARCHAR(36) PRIMARY KEY,
    code_hash VARCHAR(128) NOT NULL UNIQUE,
    client_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    redirect_uri VARCHAR(1000) NOT NULL,
    resource VARCHAR(255) NOT NULL,
    scopes JSONB NOT NULL,
    code_challenge VARCHAR(255) NOT NULL,
    code_challenge_method VARCHAR(20) NOT NULL DEFAULT 'S256',
    expires_at TIMESTAMP NOT NULL,
    consumed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_code_expires ON sys_mcp_oauth_authorization_codes(expires_at);

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_access_tokens (
    id VARCHAR(36) PRIMARY KEY,
    jti VARCHAR(128) NOT NULL UNIQUE,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    client_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(64),
    grant_id VARCHAR(36),
    resource VARCHAR(255) NOT NULL,
    scopes JSONB NOT NULL,
    session_id VARCHAR(128),
    issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_client ON sys_mcp_oauth_access_tokens(client_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_user ON sys_mcp_oauth_access_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_access_expires ON sys_mcp_oauth_access_tokens(expires_at);

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_refresh_tokens (
    id VARCHAR(36) PRIMARY KEY,
    token_hash VARCHAR(128) NOT NULL UNIQUE,
    grant_id VARCHAR(36) NOT NULL,
    client_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    rotated_from_id VARCHAR(36),
    issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    revoked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_client ON sys_mcp_oauth_refresh_tokens(client_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_expires ON sys_mcp_oauth_refresh_tokens(expires_at);

CREATE TABLE IF NOT EXISTS sys_mcp_inbound_audit_logs (
    id VARCHAR(36) PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL,
    client_request_id VARCHAR(128),
    client_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(64),
    auth_type VARCHAR(32) NOT NULL,
    method_name VARCHAR(128) NOT NULL,
    agent_id VARCHAR(128),
    conversation_id VARCHAR(128),
    dataset_id VARCHAR(128),
    scopes JSONB NOT NULL,
    status_code INTEGER NOT NULL,
    result_status VARCHAR(32) NOT NULL,
    error_code VARCHAR(64),
    latency_ms INTEGER,
    ip_hash VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mcp_inbound_audit_request ON sys_mcp_inbound_audit_logs(request_id);
CREATE INDEX IF NOT EXISTS idx_mcp_inbound_audit_client ON sys_mcp_inbound_audit_logs(client_id);
CREATE INDEX IF NOT EXISTS idx_mcp_inbound_audit_user ON sys_mcp_inbound_audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_inbound_audit_method ON sys_mcp_inbound_audit_logs(method_name);
CREATE INDEX IF NOT EXISTS idx_mcp_inbound_audit_created ON sys_mcp_inbound_audit_logs(created_at);

COMMENT ON TABLE sys_mcp_oauth_clients IS 'NanZi Platform MCP OAuth Client 配置';
COMMENT ON COLUMN sys_mcp_oauth_clients.id IS '主键 ID';
COMMENT ON COLUMN sys_mcp_oauth_clients.client_id IS 'OAuth Client ID，外部系统唯一标识';
COMMENT ON COLUMN sys_mcp_oauth_clients.client_name IS '外部系统名称';
COMMENT ON COLUMN sys_mcp_oauth_clients.client_type IS '客户端类型，第一期为 confidential';
COMMENT ON COLUMN sys_mcp_oauth_clients.client_secret_hash IS 'Client Secret 哈希，不保存明文';
COMMENT ON COLUMN sys_mcp_oauth_clients.redirect_uris IS '授权回调地址列表，必须精确匹配';
COMMENT ON COLUMN sys_mcp_oauth_clients.allowed_grant_types IS '允许的 OAuth 授权模式列表';
COMMENT ON COLUMN sys_mcp_oauth_clients.allowed_scopes IS '允许申请的 Scope 列表';
COMMENT ON COLUMN sys_mcp_oauth_clients.allowed_agent_ids IS '允许访问的智能体 ID 白名单，NULL 表示不增加限制';
COMMENT ON COLUMN sys_mcp_oauth_clients.allowed_knowledge_base_ids IS '允许检索的知识库 ID 白名单，NULL 表示不增加限制，空数组表示无权限';
COMMENT ON COLUMN sys_mcp_oauth_clients.allowed_metadata_dataset_ids IS '允许访问的元数据集 ID 白名单，NULL 表示不增加限制';
COMMENT ON COLUMN sys_mcp_oauth_clients.status IS 'Client 状态：active 启用，disabled 停用';
COMMENT ON COLUMN sys_mcp_oauth_clients.created_by IS '创建人用户 ID';
COMMENT ON COLUMN sys_mcp_oauth_clients.created_at IS '创建时间';
COMMENT ON COLUMN sys_mcp_oauth_clients.updated_at IS '最后更新时间';
COMMENT ON COLUMN sys_mcp_oauth_clients.disabled_at IS '停用时间';

COMMENT ON TABLE sys_mcp_oauth_grants IS 'NanZi Platform MCP 用户 OAuth 授权关系';
COMMENT ON COLUMN sys_mcp_oauth_grants.id IS '主键 ID';
COMMENT ON COLUMN sys_mcp_oauth_grants.client_id IS '被授权的 OAuth Client ID';
COMMENT ON COLUMN sys_mcp_oauth_grants.user_id IS 'NanZi 用户 ID';
COMMENT ON COLUMN sys_mcp_oauth_grants.scopes IS '用户同意授予的 Scope 列表';
COMMENT ON COLUMN sys_mcp_oauth_grants.resource IS '授权绑定的 Platform MCP 资源 URI';
COMMENT ON COLUMN sys_mcp_oauth_grants.status IS '授权状态：active 生效，revoked 已撤销';
COMMENT ON COLUMN sys_mcp_oauth_grants.consented_at IS '用户同意授权时间';
COMMENT ON COLUMN sys_mcp_oauth_grants.last_used_at IS '最近使用时间';
COMMENT ON COLUMN sys_mcp_oauth_grants.revoked_at IS '撤销时间';
COMMENT ON COLUMN sys_mcp_oauth_grants.created_at IS '创建时间';
COMMENT ON COLUMN sys_mcp_oauth_grants.updated_at IS '最后更新时间';

COMMENT ON TABLE sys_mcp_oauth_authorization_codes IS 'NanZi Platform MCP OAuth 一次性授权码';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.id IS '主键 ID';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.code_hash IS '一次性授权码哈希，不保存原文';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.client_id IS 'OAuth Client ID';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.user_id IS '完成授权的 NanZi 用户 ID';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.redirect_uri IS '本次授权使用的精确回调地址';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.resource IS '授权码绑定的 Platform MCP 资源 URI';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.scopes IS '本次用户同意的 Scope 列表';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.code_challenge IS 'PKCE code challenge';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.code_challenge_method IS 'PKCE 算法，固定使用 S256';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.expires_at IS '授权码过期时间';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.consumed_at IS '授权码消费时间，NULL 表示未消费';
COMMENT ON COLUMN sys_mcp_oauth_authorization_codes.created_at IS '创建时间';

COMMENT ON TABLE sys_mcp_oauth_access_tokens IS 'NanZi Platform MCP OAuth Access Token 摘要';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.id IS '主键 ID';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.jti IS 'Token 唯一标识，用于审计和撤销关联';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.token_hash IS 'Opaque Access Token 的 SHA-256 哈希';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.client_id IS 'OAuth Client ID';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.user_id IS 'NanZi 用户 ID，Client Credentials 模式为空';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.grant_id IS '关联的用户授权关系 ID';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.resource IS 'Token 绑定的 Platform MCP 资源 URI';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.scopes IS 'Token 携带的 Scope 列表';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.session_id IS '关联的 NanZi 会话 ID';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.issued_at IS '签发时间';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.expires_at IS '过期时间';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.revoked_at IS '撤销时间，NULL 表示未撤销';
COMMENT ON COLUMN sys_mcp_oauth_access_tokens.created_at IS '创建时间';

COMMENT ON TABLE sys_mcp_oauth_refresh_tokens IS 'NanZi Platform MCP OAuth Refresh Token 摘要';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.id IS '主键 ID';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.token_hash IS 'Opaque Refresh Token 的 SHA-256 哈希';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.grant_id IS '关联的用户授权关系 ID';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.client_id IS 'OAuth Client ID';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.user_id IS 'NanZi 用户 ID';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.rotated_from_id IS '轮换前的 Refresh Token ID';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.issued_at IS '签发时间';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.expires_at IS '过期时间';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.used_at IS '消费时间，轮换后不可再次使用';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.revoked_at IS '撤销时间，NULL 表示未撤销';
COMMENT ON COLUMN sys_mcp_oauth_refresh_tokens.created_at IS '创建时间';

COMMENT ON TABLE sys_mcp_inbound_audit_logs IS 'NanZi Platform MCP 入站调用审计';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.id IS '主键 ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.request_id IS 'NanZi 为本次请求生成的请求 ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.client_request_id IS '外部系统传入的幂等请求 ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.client_id IS 'OAuth Client ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.user_id IS 'NanZi 用户 ID，系统调用可为空';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.auth_type IS '认证类型，例如 oauth2_user 或 client_credentials';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.method_name IS 'MCP 方法名';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.agent_id IS '关联的智能体 ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.conversation_id IS '关联的会话 ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.dataset_id IS '关联的数据集或知识库 ID';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.scopes IS '本次请求经过校验的 Scope 列表';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.status_code IS 'HTTP 或 MCP 请求结果状态码';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.result_status IS '结果状态，例如 success 或 error';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.error_code IS '平台错误码';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.latency_ms IS '处理耗时，单位毫秒';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.ip_hash IS '请求来源 IP 哈希，不保存原始 IP';
COMMENT ON COLUMN sys_mcp_inbound_audit_logs.created_at IS '审计记录创建时间';

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT seed.resource_type, seed.resource_id, seed.enabled, NOW(), NOW()
FROM (VALUES
    ('menu', 'menu:mcp_service', TRUE),
    ('element', 'element:mcp_service:overview:read', TRUE),
    ('element', 'element:mcp_service:config:read', TRUE),
    ('element', 'element:mcp_service:config:edit', TRUE),
    ('element', 'element:mcp_service:client:read', TRUE),
    ('element', 'element:mcp_service:client:manage', TRUE),
    ('element', 'element:mcp_service:client:secret_reset', TRUE),
    ('element', 'element:mcp_service:capability:read', TRUE),
    ('element', 'element:mcp_service:capability:manage', TRUE),
    ('element', 'element:mcp_service:grant:read', TRUE),
    ('element', 'element:mcp_service:grant:revoke', TRUE),
    ('element', 'element:mcp_service:audit:read', TRUE)
) AS seed(resource_type, resource_id, enabled)
WHERE NOT EXISTS (
    SELECT 1
    FROM ai_agent_resource_permissions existing
    WHERE existing.resource_type = seed.resource_type
      AND existing.resource_id = seed.resource_id
);
