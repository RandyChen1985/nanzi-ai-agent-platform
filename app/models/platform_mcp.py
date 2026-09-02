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


__all__ = [
    "McpPlatformConfig",
    "McpOAuthAccessToken",
    "McpOAuthAuthorizationCode",
    "McpOAuthClient",
    "McpOAuthGrant",
    "McpOAuthRefreshToken",
    "McpInboundAuditLog",
]
