-- V118: 结构化凭据、管理参数与 MCP 模型可见结果的合并迁移。
-- 前置：已完成本功能之前的主库迁移；MySQL 与 PostgreSQL 只执行对应的一套。
-- 替代本地旧 V116–V119；已执行其中任意版本的环境可重跑本文件。
-- 保留存量凭据、类型版本、授权、参数值及模型结果策略；仅补缺失表、列和种子。

-- 结构化凭据的扩展迁移。历史行继续按 legacy 格式读取。
ALTER TABLE sys_secrets ADD COLUMN IF NOT EXISTS credential_format VARCHAR(24) NOT NULL DEFAULT 'legacy';

CREATE TABLE IF NOT EXISTS sys_credential_types (
    type_key VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    source VARCHAR(16) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    latest_version INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    created_by VARCHAR(64),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sys_credential_type_versions (
    type_key VARCHAR(64) NOT NULL,
    version INTEGER NOT NULL,
    schema_json JSON NOT NULL,
    schema_hash VARCHAR(64) NOT NULL,
    created_by VARCHAR(64),
    published_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (type_key, version)
);

CREATE TABLE IF NOT EXISTS sys_secret_structured_payloads (
    secret_id VARCHAR(36) PRIMARY KEY,
    owner_scope VARCHAR(16) NOT NULL,
    owner_id VARCHAR(64) NOT NULL,
    type_key VARCHAR(64) NOT NULL,
    type_version INTEGER NOT NULL,
    account VARCHAR(128) NOT NULL,
    account_digest VARCHAR(64) NOT NULL,
    value_revision INTEGER NOT NULL,
    key_id VARCHAR(48) NOT NULL,
    payload_encrypted TEXT NOT NULL,
    public_projection JSON NOT NULL,
    secret_fields_set JSON NOT NULL,
    extra_schema JSON NOT NULL,
    CONSTRAINT uk_structured_secret_account
        UNIQUE (owner_scope, owner_id, type_key, type_version, account_digest)
);

-- 种子重跑只补缺失项，保留管理员设置和已发布版本。
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('scalar', '单值凭据', 'legacy') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('scalar', 1, '{"type":"object","properties":{"value":{"type":"string","title":"凭据值","writeOnly":true}},"required":["value"],"additionalProperties":false}', '624bec17837eff592b68ab17ed9c613153dd51e741bb70c22642a57c1c6c96c5') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('variable', '变量', 'legacy') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('variable', 1, '{"type":"object","properties":{"value":{"type":"string","title":"变量值"}},"required":["value"],"additionalProperties":false}', '845f46dec1d541217595ad72d0b68b72079d65f0ca6797a494758156e44fdda7') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('imap_password', 'IMAP 邮箱', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('imap_password', 1, '{"type":"object","properties":{"host":{"type":"string","title":"服务器"},"port":{"type":"integer","title":"端口","minimum":1,"maximum":65535,"default":993},"username":{"type":"string","title":"用户名"},"password":{"type":"string","title":"应用专用密码","writeOnly":true}},"required":["host","username","password"],"additionalProperties":false}', '4a3b418d392ffca8b5118d4c8a06b1790dd16c4db61a031569701aa1d3d1a0ab') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('oauth2', 'OAuth2 刷新令牌', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('oauth2', 1, '{"type":"object","properties":{"token_url":{"type":"string","title":"Token 地址"},"client_id":{"type":"string","title":"Client ID"},"client_secret":{"type":"string","title":"Client Secret","writeOnly":true},"refresh_token":{"type":"string","title":"Refresh Token","writeOnly":true}},"required":["token_url","client_id","client_secret","refresh_token"],"additionalProperties":false}', '89af95b95111d42426d08a54c14964d6621828af32d0c6d13a145ebd8806ae4e') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('api_key', 'API Key', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('api_key', 1, '{"type":"object","properties":{"base_url":{"type":"string","title":"Base URL"},"header":{"type":"string","title":"Header 名","default":"Authorization"},"api_key":{"type":"string","title":"API Key","writeOnly":true}},"required":["api_key"],"additionalProperties":false}', '1e7d6caedfd27a8581ec0a9da4edb85344b5d55c39d67c4e34991bdfda0da9b5') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('gitlab_token', 'GitLab Token', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('gitlab_token', 1, '{"type":"object","properties":{"base_url":{"type":"string","title":"GitLab 地址"},"token":{"type":"string","title":"访问令牌","writeOnly":true}},"required":["base_url","token"],"additionalProperties":false}', '10533371ba76dc9e5d79400da8621a234bfde1d9ac25f15efaad5311ab4cc11f') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('database', '数据库', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('database', 1, '{"type":"object","properties":{"engine":{"type":"string","title":"类型"},"host":{"type":"string","title":"主机"},"port":{"type":"integer","title":"端口","minimum":1,"maximum":65535,"default":5432},"database":{"type":"string","title":"库名"},"username":{"type":"string","title":"用户"},"password":{"type":"string","title":"密码","writeOnly":true}},"required":["host","database","username","password"],"additionalProperties":false}', 'a9c003d180b7a27aa2a8c683472589e47f9d7f7cf651c885d6608b0e7fd708cc') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('webhook', 'Webhook', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('webhook', 1, '{"type":"object","properties":{"url":{"type":"string","title":"回调地址"},"secret":{"type":"string","title":"签名密钥","writeOnly":true}},"required":["url"],"additionalProperties":false}', '53bb9d079df9bad5f4479a1d601ad87e65fb77b6ebd0d7c9c4301443e88a68aa') ON CONFLICT (type_key, version) DO NOTHING;
INSERT INTO sys_credential_types (type_key, name, source) VALUES ('mcp_token', 'MCP 服务令牌', 'builtin') ON CONFLICT (type_key) DO NOTHING;
INSERT INTO sys_credential_type_versions (type_key, version, schema_json, schema_hash) VALUES ('mcp_token', 1, '{"type":"object","properties":{"token":{"type":"string","title":"服务令牌","writeOnly":true}},"required":["token"],"additionalProperties":false}', '00a87c21484fe388541a161d4462a9d3eaff3927c1ebd0d7a865038a3e0c57e6') ON CONFLICT (type_key, version) DO NOTHING;

CREATE TABLE IF NOT EXISTS sys_mcp_credential_profiles (
    tool_id VARCHAR(36) PRIMARY KEY,
    profile_id VARCHAR(36) NOT NULL,
    server_id VARCHAR(36) NOT NULL,
    tool_name VARCHAR(255) NOT NULL,
    type_key VARCHAR(64) NOT NULL,
    type_version INTEGER NOT NULL,
    selection_mode VARCHAR(16) NOT NULL,
    mappings JSONB NOT NULL,
    schema_digest VARCHAR(64) NOT NULL,
    endpoint_digest VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS sys_mcp_credential_use_grants (
    tool_id VARCHAR(36) NOT NULL,
    secret_id VARCHAR(36) NOT NULL,
    grantee_type VARCHAR(16) NOT NULL,
    grantee_id VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP,
    PRIMARY KEY (tool_id, secret_id, grantee_type, grantee_id)
);

CREATE TABLE IF NOT EXISTS sys_task_credential_waits (
    request_id VARCHAR(64) PRIMARY KEY,
    task_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    run_conversation_id VARCHAR(64) NOT NULL,
    task_definition_digest VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    resume_lease_until TIMESTAMP,
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_task_credential_waits_task ON sys_task_credential_waits (task_id, status, expires_at);

ALTER TABLE sys_secret_bindings ADD COLUMN IF NOT EXISTS value_field VARCHAR(64);
ALTER TABLE sys_secret_bindings ADD COLUMN IF NOT EXISTS target_digest VARCHAR(64);

-- NULL 表示尚未通过管理界面保存，运行时继续读取原部署配置。
INSERT INTO "system_configs" ("key", "value", "description", "category", "is_secret")
VALUES
    ('structured_credentials_enabled', NULL, '是否启用结构化凭据', 'security', FALSE),
    ('credential_payload_keyring_json', NULL, '结构化凭据加密密钥环', 'security', TRUE)
ON CONFLICT ("key") DO NOTHING;

-- 存量工具保留仅返回状态；只为受信任的工具显式配置模型可见字段。
ALTER TABLE sys_mcp_credential_profiles
    ADD COLUMN IF NOT EXISTS model_result_fields JSONB NULL;

COMMENT ON COLUMN sys_mcp_credential_profiles.model_result_fields
    IS '模型可见结果字段路径，NULL 或空数组表示仅返回状态';

-- 让使用连接认证或逐参数凭据的 MCP 工具独立配置模型可见结果。
ALTER TABLE sys_mcp_tool_cache
    ADD COLUMN IF NOT EXISTS model_result_policy JSONB NULL;

COMMENT ON COLUMN sys_mcp_tool_cache.model_result_policy
    IS '模型可见结果路径、目标指纹及版本';
