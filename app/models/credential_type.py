"""版本化凭证类型与结构化实例存储。"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint

from app.core.orm import Base


class CredentialType(Base):
    __tablename__ = "sys_credential_types"

    type_key = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    source = Column(String(16), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    latest_version = Column(Integer, nullable=False, default=1)
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(64), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class CredentialTypeVersion(Base):
    __tablename__ = "sys_credential_type_versions"

    type_key = Column(String(64), primary_key=True)
    version = Column(Integer, primary_key=True)
    schema_json = Column(JSON, nullable=False)
    schema_hash = Column(String(64), nullable=False)
    created_by = Column(String(64), nullable=True)
    published_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class StructuredCredential(Base):
    __tablename__ = "sys_secret_structured_payloads"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope", "owner_id", "type_key", "type_version", "account_digest",
            name="uk_structured_secret_account",
        ),
    )

    secret_id = Column(String(36), primary_key=True)
    owner_scope = Column(String(16), nullable=False)
    owner_id = Column(String(64), nullable=False)
    type_key = Column(String(64), nullable=False)
    type_version = Column(Integer, nullable=False)
    account = Column(String(128), nullable=False)
    account_digest = Column(String(64), nullable=False)
    value_revision = Column(Integer, nullable=False, default=1)
    key_id = Column(String(48), nullable=False)
    payload_encrypted = Column(Text, nullable=False)
    public_projection = Column(JSON, nullable=False)
    secret_fields_set = Column(JSON, nullable=False)
    extra_schema = Column(JSON, nullable=False)



class McpCredentialProfile(Base):
    __tablename__ = "sys_mcp_credential_profiles"

    tool_id = Column(String(36), primary_key=True)
    profile_id = Column(String(36), nullable=False)
    server_id = Column(String(36), nullable=False)
    tool_name = Column(String(255), nullable=False)
    type_key = Column(String(64), nullable=False)
    type_version = Column(Integer, nullable=False)
    selection_mode = Column(String(16), nullable=False)
    mappings = Column(JSON, nullable=False)
    model_result_fields = Column(JSON, nullable=True)
    schema_digest = Column(String(64), nullable=False)
    endpoint_digest = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class McpCredentialUseGrant(Base):
    __tablename__ = "sys_mcp_credential_use_grants"

    tool_id = Column(String(36), primary_key=True)
    secret_id = Column(String(36), primary_key=True)
    grantee_type = Column(String(16), primary_key=True)
    grantee_id = Column(String(64), primary_key=True)
    expires_at = Column(DateTime, nullable=True)


class TaskCredentialWait(Base):
    __tablename__ = "sys_task_credential_waits"

    request_id = Column(String(64), primary_key=True)
    task_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    run_conversation_id = Column(String(64), nullable=False)
    task_definition_digest = Column(String(64), nullable=False)
    trace_id = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    resume_lease_until = Column(DateTime)
