CREATE TABLE IF NOT EXISTS sys_mcp_outbound_audit_logs (
    id VARCHAR(36) PRIMARY KEY,
    server_id VARCHAR(36) NOT NULL,
    server_name VARCHAR(100),
    tool_name VARCHAR(255) NOT NULL,
    agent_id VARCHAR(36),
    agent_name VARCHAR(100),
    user_id VARCHAR(64),
    user_name VARCHAR(64),
    trace_id VARCHAR(64),
    status VARCHAR(32) NOT NULL DEFAULT 'success',
    latency_ms INTEGER,
    error_message TEXT,
    tool_input JSONB,
    tool_output JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.id IS '出站审计事件 ID';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.server_id IS '关联 MCP Server ID';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.server_name IS 'MCP Server 名称';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.tool_name IS '具体被调用的 MCP 工具名';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.agent_id IS '发起调用的智能体 ID';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.agent_name IS '发起调用的智能体名称';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.user_id IS '发起调用的用户 ID';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.user_name IS '发起调用的用户名';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.trace_id IS '关联请求/会话 Trace ID';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.status IS '调用结果：success, failed, timeout';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.latency_ms IS '执行耗时(毫秒)';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.error_message IS '错误信息与异常堆栈';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.tool_input IS '工具入参快照';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.tool_output IS '工具出参结果快照';
COMMENT ON COLUMN sys_mcp_outbound_audit_logs.created_at IS '调用发生时间';
CREATE INDEX IF NOT EXISTS idx_mcp_outbound_server ON sys_mcp_outbound_audit_logs(server_id);
CREATE INDEX IF NOT EXISTS idx_mcp_outbound_tool ON sys_mcp_outbound_audit_logs(tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_outbound_agent ON sys_mcp_outbound_audit_logs(agent_id);
CREATE INDEX IF NOT EXISTS idx_mcp_outbound_status ON sys_mcp_outbound_audit_logs(status);
CREATE INDEX IF NOT EXISTS idx_mcp_outbound_trace ON sys_mcp_outbound_audit_logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_mcp_outbound_created ON sys_mcp_outbound_audit_logs(created_at);
