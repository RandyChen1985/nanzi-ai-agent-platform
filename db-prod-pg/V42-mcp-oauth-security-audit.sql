CREATE TABLE IF NOT EXISTS sys_mcp_oauth_security_audit_logs (
    id VARCHAR(36) PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    request_id VARCHAR(128),
    client_id VARCHAR(128),
    user_id VARCHAR(64),
    actor_user_id VARCHAR(64),
    result_status VARCHAR(32) NOT NULL DEFAULT 'completed',
    error_code VARCHAR(128),
    details JSONB,
    ip_hash VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.id IS '安全审计事件 ID';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.event_type IS '安全事件类型';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.request_id IS '关联请求 ID';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.client_id IS '关联 OAuth Client ID';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.user_id IS '被授权或被操作的用户 ID';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.actor_user_id IS '实际执行管理操作的用户 ID';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.result_status IS '事件结果：completed、failed、denied 等';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.error_code IS 'OAuth 或限流错误码';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.details IS '脱敏后的事件扩展信息';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.ip_hash IS '请求 IP 的不可逆摘要';
COMMENT ON COLUMN sys_mcp_oauth_security_audit_logs.created_at IS '事件发生时间';
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_security_event ON sys_mcp_oauth_security_audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_security_client ON sys_mcp_oauth_security_audit_logs(client_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_security_user ON sys_mcp_oauth_security_audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_mcp_oauth_security_created ON sys_mcp_oauth_security_audit_logs(created_at);
-- Any future security-audit seed rows must use ON CONFLICT (id) DO NOTHING.
