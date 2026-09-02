-- MCP OAuth 安全生命周期审计
CREATE TABLE IF NOT EXISTS sys_mcp_oauth_security_audit_logs (
    id VARCHAR(36) PRIMARY KEY COMMENT '安全审计事件 ID',
    event_type VARCHAR(64) NOT NULL COMMENT '安全事件类型',
    request_id VARCHAR(128) NULL COMMENT '关联请求 ID',
    client_id VARCHAR(128) NULL COMMENT '关联 OAuth Client ID',
    user_id VARCHAR(64) NULL COMMENT '被授权或被操作的用户 ID',
    actor_user_id VARCHAR(64) NULL COMMENT '实际执行管理操作的用户 ID',
    result_status VARCHAR(32) NOT NULL DEFAULT 'completed' COMMENT '事件结果：completed、failed、denied 等',
    error_code VARCHAR(128) NULL COMMENT 'OAuth 或限流错误码',
    details JSON NULL COMMENT '脱敏后的事件扩展信息',
    ip_hash VARCHAR(128) NULL COMMENT '请求 IP 的不可逆摘要',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '事件发生时间',
    INDEX idx_mcp_oauth_security_event (event_type),
    INDEX idx_mcp_oauth_security_client (client_id),
    INDEX idx_mcp_oauth_security_user (user_id),
    INDEX idx_mcp_oauth_security_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
