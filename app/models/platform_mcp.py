"""NanZi Platform MCP 入站 OAuth2 数据模型。"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, UniqueConstraint

from app.core.orm import Base


class McpPlatformConfig(Base):
    """NanZi Platform MCP 单例服务配置。"""

    __tablename__ = "sys_mcp_platform_config"

    id = Column(Integer, primary_key=True, default=1, comment="单例配置 ID，固定为 1")
    platform_enabled = Column(Boolean, nullable=False, default=False, comment="Platform MCP 总开关")
    agent_enabled = Column(Boolean, nullable=False, default=False, comment="智能体能力组开关")
    conversation_enabled = Column(Boolean, nullable=False, default=False, comment="会话能力组开关")
    knowledge_enabled = Column(Boolean, nullable=False, default=False, comment="知识库能力组开关")
    metadata_enabled = Column(Boolean, nullable=False, default=False, comment="元数据能力组开关")
    rate_limit_client_per_minute = Column(Integer, nullable=False, default=120, comment="单个 Client 每分钟调用上限，0 表示关闭")
    rate_limit_user_per_minute = Column(Integer, nullable=False, default=60, comment="单个用户每分钟调用上限，0 表示关闭")
    created_by = Column(String(64), nullable=True, comment="首次创建人用户 ID")
    updated_by = Column(String(64), nullable=True, comment="最后修改人用户 ID")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, comment="创建时间")
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        comment="最后更新时间",
    )


class McpOAuthClient(Base):
    __tablename__ = "sys_mcp_oauth_clients"

    id = Column(String(36), primary_key=True)
    client_id = Column(String(128), nullable=False, unique=True, index=True)
    client_name = Column(String(200), nullable=False)
    client_type = Column(String(20), nullable=False, default="confidential")
    client_secret_hash = Column(String(128), nullable=True)
    redirect_uris = Column(JSON, nullable=False, default=list)
    allowed_grant_types = Column(JSON, nullable=False, default=list)
    allowed_scopes = Column(JSON, nullable=False, default=list)
    scope_version = Column(Integer, nullable=False, default=1, comment="Client Scope 版本，每次 Scope 变更递增")
    is_shared = Column(Boolean, nullable=False, default=False, index=True, comment="是否为全员共享 Client")
    status = Column(String(20), nullable=False, default="active", index=True)
    created_by = Column(String(64), nullable=True, comment="创建人用户 ID")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    disabled_at = Column(DateTime, nullable=True)


class McpOAuthGrant(Base):
    __tablename__ = "sys_mcp_oauth_grants"
    __table_args__ = (
        UniqueConstraint("client_id", "user_id", "resource", name="uix_mcp_oauth_grant_subject"),
    )

    id = Column(String(36), primary_key=True)
    client_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    scopes = Column(JSON, nullable=False, default=list)
    resource = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active", index=True)
    consented_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class McpOAuthAuthorizationCode(Base):
    __tablename__ = "sys_mcp_oauth_authorization_codes"

    id = Column(String(36), primary_key=True)
    code_hash = Column(String(128), nullable=False, unique=True, index=True)
    client_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    redirect_uri = Column(String(1000), nullable=False)
    resource = Column(String(255), nullable=False)
    scopes = Column(JSON, nullable=False, default=list)
    code_challenge = Column(String(255), nullable=False)
    code_challenge_method = Column(String(20), nullable=False, default="S256")
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class McpOAuthAccessToken(Base):
    __tablename__ = "sys_mcp_oauth_access_tokens"

    id = Column(String(36), primary_key=True)
    jti = Column(String(128), nullable=False, unique=True, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    client_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    grant_id = Column(String(36), nullable=True, index=True)
    resource = Column(String(255), nullable=False)
    scopes = Column(JSON, nullable=False, default=list)
    scope_version = Column(Integer, nullable=False, default=1, comment="签发 Token 时的 Client Scope 版本")
    session_id = Column(String(128), nullable=True)
    issued_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class McpOAuthRefreshToken(Base):
    __tablename__ = "sys_mcp_oauth_refresh_tokens"

    id = Column(String(36), primary_key=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    grant_id = Column(String(36), nullable=False, index=True)
    client_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    rotated_from_id = Column(String(36), nullable=True)
    issued_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class McpInboundAuditLog(Base):
    __tablename__ = "sys_mcp_inbound_audit_logs"

    id = Column(String(36), primary_key=True)
    request_id = Column(String(128), nullable=False, index=True)
    client_request_id = Column(String(128), nullable=True)
    client_id = Column(String(128), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    auth_type = Column(String(32), nullable=False)
    method_name = Column(String(128), nullable=False, index=True)
    agent_id = Column(String(128), nullable=True)
    conversation_id = Column(String(128), nullable=True)
    dataset_id = Column(String(128), nullable=True)
    scopes = Column(JSON, nullable=False, default=list)
    status_code = Column(Integer, nullable=False)
    result_status = Column(String(32), nullable=False)
    error_code = Column(String(64), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    ip_hash = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class McpOAuthSecurityAuditLog(Base):
    """OAuth 与 Platform MCP 安全生命周期事件，不保存凭证原文。"""

    __tablename__ = "sys_mcp_oauth_security_audit_logs"

    id = Column(String(36), primary_key=True, comment="安全审计事件 ID")
    event_type = Column(String(64), nullable=False, index=True, comment="安全事件类型")
    request_id = Column(String(128), nullable=True, index=True, comment="关联请求 ID")
    client_id = Column(String(128), nullable=True, index=True, comment="关联 OAuth Client ID")
    user_id = Column(String(64), nullable=True, index=True, comment="被授权或被操作的用户 ID")
    actor_user_id = Column(String(64), nullable=True, comment="实际执行管理操作的用户 ID")
    result_status = Column(String(32), nullable=False, default="completed", comment="事件结果：completed、failed、denied 等")
    error_code = Column(String(128), nullable=True, comment="OAuth 或限流错误码")
    details = Column(JSON, nullable=True, comment="脱敏后的事件扩展信息")
    ip_hash = Column(String(128), nullable=True, comment="请求 IP 的不可逆摘要")
    created_at = Column(DateTime, default=datetime.utcnow, index=True, comment="事件发生时间")


__all__ = [
    "McpPlatformConfig",
    "McpOAuthAccessToken",
    "McpOAuthAuthorizationCode",
    "McpOAuthClient",
    "McpOAuthGrant",
    "McpOAuthRefreshToken",
    "McpInboundAuditLog",
    "McpOAuthSecurityAuditLog",
]
