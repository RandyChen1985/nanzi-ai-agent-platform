from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, ForeignKey, Index, JSON
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

    # 定义探测（只读）的元数据。刻意与 last_sync_at 分开：last_sync_at 只在真正写库
    # 应用差异时更新，而探测每天都跑且从不写业务字段——混用会让「多久没同步」失真。
    last_probe_at = Column(DateTime, nullable=True)
    #: ok / changed / unreachable / needs_user / skipped_disabled
    last_probe_status = Column(String(24), nullable=True)
    #: 连续失败次数，成功一次即归零；用来区分「偶发超时」与「服务已下线」。
    probe_failure_count = Column(Integer, nullable=False, default=0)
    #: 最近一次失败原因，截断 500 字；禁止存远端完整报文（可能含敏感内容）。
    last_probe_error = Column(String(500), nullable=True)
    
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
    is_published = Column(Boolean, default=False)
    is_available = Column(Boolean, nullable=False, default=True)

    # 定义指纹：description + parameter_schema + annotations 规范化后的 sha256。
    # 判断「远端变没变」不能靠 updated_at —— 那是 ON UPDATE 时间戳，只反映上次同步，
    # 远端悄悄改了描述它也不会动。指纹在同步（应用）与探测两条路径上都要写。
    definition_hash = Column(String(64), nullable=True)
    #: 最近一次发现「远端定义与本地生效值不一致」的时间，仅标记，不代表已应用。
    definition_changed_at = Column(DateTime, nullable=True)
    
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


class McpToolDefinitionChange(Base):
    """MCP 工具定义变更记录（探测发现，人工确认后应用）。

    与 ``McpToolCache`` 的关系是「候选 vs 生效」：探测只往这张表写差异，
    ``sys_mcp_tool_cache`` 里的 description / schema 在被应用之前保持原样。
    这样即使每天探 24 次，也不会把远端瞬时故障变成一次静默下线。

    去重口径：同一 (server_id, tool_name, change_kind) 只保留一条 pending，
    重复探测覆盖 after_value 而不是叠加——否则同一个改动每天堆一条。
    """

    __tablename__ = "sys_mcp_tool_definition_changes"

    id = Column(String(36), primary_key=True)
    #: 一次探测一批，前端按批次分组展示
    batch_id = Column(String(36), nullable=False)
    server_id = Column(
        String(36),
        ForeignKey("sys_mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 全名 server:tool，与 sys_mcp_tool_cache.tool_name 同口径
    tool_name = Column(String(255), nullable=False)
    #: description_changed / param_added_optional / param_removed / param_required /
    #: param_type / param_enum / tool_added / tool_removed / annotations_changed
    change_kind = Column(String(32), nullable=False)
    #: safe / notice / breaking
    severity = Column(String(16), nullable=False, default="notice")
    #: 变化部分的 JSON 快照（只存变的那几项，不存全量定义）
    before_value = Column(Text, nullable=True)
    after_value = Column(Text, nullable=True)
    detected_at = Column(DateTime, nullable=False, default=datetime.now)
    #: nightly / page / hot_call / manual
    detected_by = Column(String(24), nullable=False, default="manual")
    #: pending / applied / dismissed
    status = Column(String(16), nullable=False, default="pending")
    handled_at = Column(DateTime, nullable=True)
    #: 处理人 user_id；NULL 表示系统自动应用
    handled_by = Column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_mcp_chg_server_status", "server_id", "status"),
        Index("idx_mcp_chg_detected", "detected_at"),
        Index("idx_mcp_chg_tool", "tool_name"),
    )
