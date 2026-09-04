-- MCP 外部出站工具调用审计日志表
CREATE TABLE IF NOT EXISTS sys_mcp_outbound_audit_logs (
    id VARCHAR(36) PRIMARY KEY COMMENT '出站审计事件 ID',
    server_id VARCHAR(36) NOT NULL COMMENT '关联 MCP Server ID',
    server_name VARCHAR(100) NULL COMMENT 'MCP Server 名称',
    tool_name VARCHAR(255) NOT NULL COMMENT '具体被调用的 MCP 工具名',
    agent_id VARCHAR(36) NULL COMMENT '发起调用的智能体 ID',
    agent_name VARCHAR(100) NULL COMMENT '发起调用的智能体名称',
    user_id VARCHAR(64) NULL COMMENT '发起调用的用户 ID',
    user_name VARCHAR(64) NULL COMMENT '发起调用的用户名',
    trace_id VARCHAR(64) NULL COMMENT '关联请求/会话 Trace ID',
    status VARCHAR(32) NOT NULL DEFAULT 'success' COMMENT '调用结果：success, failed, timeout',
    latency_ms INT NULL COMMENT '执行耗时(毫秒)',
    error_message TEXT NULL COMMENT '错误信息与异常堆栈',
    tool_input JSON NULL COMMENT '工具入参快照',
    tool_output JSON NULL COMMENT '工具出参结果快照',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '调用发生时间',
    INDEX idx_mcp_outbound_server (server_id),
    INDEX idx_mcp_outbound_tool (tool_name),
    INDEX idx_mcp_outbound_agent (agent_id),
    INDEX idx_mcp_outbound_status (status),
    INDEX idx_mcp_outbound_trace (trace_id),
    INDEX idx_mcp_outbound_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
