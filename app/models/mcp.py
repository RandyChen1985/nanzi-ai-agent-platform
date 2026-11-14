from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.orm import Base

class McpServer(Base):
    __tablename__ = "sys_mcp_servers"

    id = Column(String(36), primary_key=True)
    server_name = Column(String(100), nullable=False)
    remark = Column(String(500), nullable=True)
    sse_url = Column(Text, nullable=False)
    auth_headers = Column(Text, nullable=True)  # 加密后的 JSON 字符串；兼容历史明文
    credential_mode = Column(String(40), nullable=False, default="static")
    fixed_token_encrypted = Column(Text, nullable=True)
    user_assertion_enabled = Column(Boolean, nullable=False, default=False)
    user_assertion_header = Column(String(100), nullable=False, default="X-Nanzi-User-Assertion")
    user_assertion_audience = Column(String(255), nullable=True)
    user_assertion_key_id = Column(String(100), nullable=True)
    user_assertion_issuer = Column(String(255), nullable=True)
    user_assertion_private_key_encrypted = Column(Text, nullable=True)
    enabled_status = Column(Integer, default=0) # 0: Offline/Disabled, 1: Online/Enabled
    last_sync_at = Column(DateTime, nullable=True)
    
    scope = Column(String(20), default="global", nullable=False)
    user_id = Column(Integer, nullable=True)
    # 业务分组：单值归属，只服务「MCP 工具集」左栏页签与筛选。
    # 与 remark（自由备注）、scope（global/personal 归属范围）都不是一回事，见 V215 迁移注释。
    group_name = Column(String(64), nullable=True)
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    tools = relationship("McpToolCache", back_populates="server", cascade="all, delete-orphan")

class McpToolCache(Base):
    __tablename__ = "sys_mcp_tool_cache"

    id = Column(String(36), primary_key=True)
    server_id = Column(String(36), ForeignKey("sys_mcp_servers.id"), nullable=False)
    tool_name = Column(String(255), nullable=False)
    tool_description = Column(Text, nullable=True)
    parameter_schema = Column(Text, nullable=True) # JSON Schema string
    #: 值由凭据中心提供的参数名，JSON 数组。声明过的参数从模型可见的 schema 里
    #: 剔除，运行时按 ``arg:<短工具名>:<参数名>`` 槽位注入。
    #: 不塞进 ``parameter_schema``：同步时整体覆盖，且 ``x-nanzi-*`` 会被剥掉。
    credential_params = Column(Text, nullable=True)
    # 无组凭据时的结果路径与批准时的工具/目标指纹，保存在同一配置快照。
    model_result_policy = Column(JSON, nullable=True)
    is_published = Column(Boolean, default=False)
    is_available = Column(Boolean, nullable=False, default=True)
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    server = relationship("McpServer", back_populates="tools")


class McpOutboundAuditLog(Base):
    """MCP 外部出站工具调用审计日志"""
    __tablename__ = "sys_mcp_outbound_audit_logs"

    id = Column(String(36), primary_key=True)
    server_id = Column(String(36), ForeignKey("sys_mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True)
    server_name = Column(String(100), nullable=True)
    tool_name = Column(String(255), nullable=False, index=True)
    agent_id = Column(String(36), nullable=True, index=True)
    agent_name = Column(String(100), nullable=True)
    user_id = Column(String(64), nullable=True, index=True)
    user_name = Column(String(64), nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="success", index=True)
    latency_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    tool_input = Column(JSON, nullable=True)
    tool_output = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
