-- V137: NanZi Platform MCP 入站 OAuth2 Client、Token 与审计表
-- 说明：这是 NanZi 作为 MCP Server 对外提供服务的入站认证数据；不修改出站 MCP 表。

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS sys_mcp_platform_config (
    id TINYINT NOT NULL PRIMARY KEY COMMENT '单例配置 ID，固定为 1',
    platform_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'Platform MCP 总开关',
    agent_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '智能体能力组开关',
    conversation_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '会话能力组开关',
    knowledge_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '知识库能力组开关',
    metadata_enabled TINYINT(1) NOT NULL DEFAULT 0 COMMENT '元数据能力组开关',
    created_by VARCHAR(64) NULL COMMENT '首次创建人用户 ID',
    updated_by VARCHAR(64) NULL COMMENT '最后修改人用户 ID',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP 服务专属配置';

INSERT IGNORE INTO sys_mcp_platform_config (id) VALUES (1);

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_clients (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '主键 ID',
    client_id VARCHAR(128) NOT NULL COMMENT 'OAuth Client ID，外部系统唯一标识',
    client_name VARCHAR(200) NOT NULL COMMENT '外部系统名称',
    client_type VARCHAR(20) NOT NULL DEFAULT 'confidential' COMMENT '客户端类型，第一期为 confidential',
    client_secret_hash VARCHAR(128) NULL COMMENT 'Client Secret 哈希，不保存明文',
    redirect_uris JSON NOT NULL COMMENT '授权回调地址列表，必须精确匹配',
    allowed_grant_types JSON NOT NULL COMMENT '允许的 OAuth 授权模式列表',
    allowed_scopes JSON NOT NULL COMMENT '允许申请的 Scope 列表',
    allowed_agent_ids JSON NULL COMMENT '允许访问的智能体 ID 白名单，NULL 表示不增加限制',
    allowed_knowledge_base_ids JSON NULL COMMENT '允许检索的知识库 ID 白名单，NULL 表示不增加限制，空数组表示无权限',
    allowed_metadata_dataset_ids JSON NULL COMMENT '允许访问的元数据集 ID 白名单，NULL 表示不增加限制',
    status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'Client 状态：active 启用，disabled 停用',
    created_by VARCHAR(64) NULL COMMENT '创建人用户 ID',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    disabled_at TIMESTAMP NULL COMMENT '停用时间',
    UNIQUE KEY uix_mcp_oauth_client_id (client_id),
    KEY idx_mcp_oauth_client_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP OAuth 客户端配置';

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_grants (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '主键 ID',
    client_id VARCHAR(128) NOT NULL COMMENT '被授权的 OAuth Client ID',
    user_id VARCHAR(64) NOT NULL COMMENT 'NanZi 用户 ID',
    scopes JSON NOT NULL COMMENT '用户同意授予的 Scope 列表',
    resource VARCHAR(255) NOT NULL COMMENT '授权绑定的 Platform MCP 资源 URI',
    status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT '授权状态：active 生效，revoked 已撤销',
    consented_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '用户同意授权时间',
    last_used_at TIMESTAMP NULL COMMENT '最近使用时间',
    revoked_at TIMESTAMP NULL COMMENT '撤销时间',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    UNIQUE KEY uix_mcp_oauth_grant_subject (client_id, user_id, resource),
    KEY idx_mcp_oauth_grant_user (user_id),
    KEY idx_mcp_oauth_grant_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP 用户 OAuth 授权关系';

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_authorization_codes (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '主键 ID',
    code_hash VARCHAR(128) NOT NULL COMMENT '一次性授权码哈希，不保存原文',
    client_id VARCHAR(128) NOT NULL COMMENT 'OAuth Client ID',
    user_id VARCHAR(64) NOT NULL COMMENT '完成授权的 NanZi 用户 ID',
    redirect_uri VARCHAR(1000) NOT NULL COMMENT '本次授权使用的精确回调地址',
    resource VARCHAR(255) NOT NULL COMMENT '授权码绑定的 Platform MCP 资源 URI',
    scopes JSON NOT NULL COMMENT '本次用户同意的 Scope 列表',
    code_challenge VARCHAR(255) NOT NULL COMMENT 'PKCE code challenge',
    code_challenge_method VARCHAR(20) NOT NULL DEFAULT 'S256' COMMENT 'PKCE 算法，固定使用 S256',
    expires_at TIMESTAMP NOT NULL COMMENT '授权码过期时间',
    consumed_at TIMESTAMP NULL COMMENT '授权码消费时间，NULL 表示未消费',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY uix_mcp_oauth_code_hash (code_hash),
    KEY idx_mcp_oauth_code_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP OAuth 一次性授权码';

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_access_tokens (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '主键 ID',
    jti VARCHAR(128) NOT NULL COMMENT 'Token 唯一标识，用于审计和撤销关联',
    token_hash VARCHAR(128) NOT NULL COMMENT 'Opaque Access Token 的 SHA-256 哈希',
    client_id VARCHAR(128) NOT NULL COMMENT 'OAuth Client ID',
    user_id VARCHAR(64) NULL COMMENT 'NanZi 用户 ID，Client Credentials 模式为空',
    grant_id VARCHAR(36) NULL COMMENT '关联的用户授权关系 ID',
    resource VARCHAR(255) NOT NULL COMMENT 'Token 绑定的 Platform MCP 资源 URI',
    scopes JSON NOT NULL COMMENT 'Token 携带的 Scope 列表',
    session_id VARCHAR(128) NULL COMMENT '关联的 NanZi 会话 ID',
    issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '签发时间',
    expires_at TIMESTAMP NOT NULL COMMENT '过期时间',
    revoked_at TIMESTAMP NULL COMMENT '撤销时间，NULL 表示未撤销',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY uix_mcp_oauth_access_jti (jti),
    UNIQUE KEY uix_mcp_oauth_access_hash (token_hash),
    KEY idx_mcp_oauth_access_client (client_id),
    KEY idx_mcp_oauth_access_user (user_id),
    KEY idx_mcp_oauth_access_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP OAuth Access Token 摘要';

CREATE TABLE IF NOT EXISTS sys_mcp_oauth_refresh_tokens (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '主键 ID',
    token_hash VARCHAR(128) NOT NULL COMMENT 'Opaque Refresh Token 的 SHA-256 哈希',
    grant_id VARCHAR(36) NOT NULL COMMENT '关联的用户授权关系 ID',
    client_id VARCHAR(128) NOT NULL COMMENT 'OAuth Client ID',
    user_id VARCHAR(64) NOT NULL COMMENT 'NanZi 用户 ID',
    rotated_from_id VARCHAR(36) NULL COMMENT '轮换前的 Refresh Token ID',
    issued_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '签发时间',
    expires_at TIMESTAMP NOT NULL COMMENT '过期时间',
    used_at TIMESTAMP NULL COMMENT '消费时间，轮换后不可再次使用',
    revoked_at TIMESTAMP NULL COMMENT '撤销时间，NULL 表示未撤销',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    UNIQUE KEY uix_mcp_oauth_refresh_hash (token_hash),
    KEY idx_mcp_oauth_refresh_client (client_id),
    KEY idx_mcp_oauth_refresh_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP OAuth Refresh Token 摘要';

CREATE TABLE IF NOT EXISTS sys_mcp_inbound_audit_logs (
    id VARCHAR(36) NOT NULL PRIMARY KEY COMMENT '主键 ID',
    request_id VARCHAR(128) NOT NULL COMMENT 'NanZi 为本次请求生成的请求 ID',
    client_request_id VARCHAR(128) NULL COMMENT '外部系统传入的幂等请求 ID',
    client_id VARCHAR(128) NOT NULL COMMENT 'OAuth Client ID',
    user_id VARCHAR(64) NULL COMMENT 'NanZi 用户 ID，系统调用可为空',
    auth_type VARCHAR(32) NOT NULL COMMENT '认证类型，例如 oauth2_user 或 client_credentials',
    method_name VARCHAR(128) NOT NULL COMMENT 'MCP 方法名',
    agent_id VARCHAR(128) NULL COMMENT '关联的智能体 ID',
    conversation_id VARCHAR(128) NULL COMMENT '关联的会话 ID',
    dataset_id VARCHAR(128) NULL COMMENT '关联的数据集或知识库 ID',
    scopes JSON NOT NULL COMMENT '本次请求经过校验的 Scope 列表',
    status_code INT NOT NULL COMMENT 'HTTP 或 MCP 请求结果状态码',
    result_status VARCHAR(32) NOT NULL COMMENT '结果状态，例如 success 或 error',
    error_code VARCHAR(64) NULL COMMENT '平台错误码',
    latency_ms INT NULL COMMENT '处理耗时，单位毫秒',
    ip_hash VARCHAR(128) NULL COMMENT '请求来源 IP 哈希，不保存原始 IP',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '审计记录创建时间',
    KEY idx_mcp_inbound_audit_request (request_id),
    KEY idx_mcp_inbound_audit_client (client_id),
    KEY idx_mcp_inbound_audit_user (user_id),
    KEY idx_mcp_inbound_audit_method (method_name),
    KEY idx_mcp_inbound_audit_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='NanZi Platform MCP 入站调用审计日志';

INSERT INTO ai_agent_resource_permissions (resource_type, resource_id, enabled, created_at, updated_at)
SELECT seed.resource_type, seed.resource_id, seed.enabled, NOW(), NOW()
FROM (
    SELECT 'menu' AS resource_type, 'menu:mcp_service' AS resource_id, 1 AS enabled
    UNION ALL SELECT 'element', 'element:mcp_service:overview:read', 1
    UNION ALL SELECT 'element', 'element:mcp_service:config:read', 1
    UNION ALL SELECT 'element', 'element:mcp_service:config:edit', 1
    UNION ALL SELECT 'element', 'element:mcp_service:client:read', 1
    UNION ALL SELECT 'element', 'element:mcp_service:client:manage', 1
    UNION ALL SELECT 'element', 'element:mcp_service:client:secret_reset', 1
    UNION ALL SELECT 'element', 'element:mcp_service:capability:read', 1
    UNION ALL SELECT 'element', 'element:mcp_service:capability:manage', 1
    UNION ALL SELECT 'element', 'element:mcp_service:grant:read', 1
    UNION ALL SELECT 'element', 'element:mcp_service:grant:revoke', 1
    UNION ALL SELECT 'element', 'element:mcp_service:audit:read', 1
) AS seed
WHERE NOT EXISTS (
    SELECT 1
    FROM ai_agent_resource_permissions existing
    WHERE existing.resource_type = seed.resource_type
      AND existing.resource_id = seed.resource_id
);
