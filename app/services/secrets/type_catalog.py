"""凭据类型目录。所有创建、发布和状态切换以类型行为并发边界。"""

from __future__ import annotations

import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.credential_type import CredentialType, CredentialTypeVersion
from app.models.secret import SecretAuditLog
from app.services.secrets.credential_schema import CredentialSchemaError, schema_digest, validate_schema

TYPE_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
LEGACY_TYPES = {"scalar", "variable"}


class CredentialTypeError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def audit_change(session, action: str, actor_id: str | None, detail: dict, *, secret=None) -> None:
    session.add(SecretAuditLog(
        id=str(uuid.uuid4()), action=action, actor_id=actor_id,
        secret_id=secret.id if secret else None,
        owner_scope=secret.owner_scope if secret else None,
        detail=json.dumps(detail, ensure_ascii=False),
    ))


async def get_type(session, type_key: str, *, for_update: bool = False) -> CredentialType:
    stmt = select(CredentialType).where(CredentialType.type_key == type_key)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(stmt)
    if row is None:
        raise CredentialTypeError("CREDENTIAL_TYPE_NOT_FOUND", "凭据类型不存在", 404)
    return row


async def get_version(session, type_key: str, version: int) -> CredentialTypeVersion:
    row = await session.get(CredentialTypeVersion, (type_key, version))
    if row is None:
        raise CredentialTypeError("CREDENTIAL_TYPE_VERSION_NOT_FOUND", "凭据类型版本不存在", 404)
    return row


async def creation_version(session, type_key: str, version: int) -> CredentialTypeVersion:
    row = await get_type(session, type_key, for_update=True)
    if not row.enabled:
        raise CredentialTypeError("CREDENTIAL_TYPE_DISABLED", "该凭据类型已禁用，无法创建凭据", 409)
    return await get_version(session, type_key, version)


def _check_revision(row, expected_revision: int) -> None:
    if row.revision != expected_revision:
        raise CredentialTypeError("CREDENTIAL_REVISION_CONFLICT", "数据已更新，请刷新后重试", 409)


def normalize_schema(schema: dict) -> dict:
    try:
        return validate_schema(schema)
    except CredentialSchemaError as exc:
        raise CredentialTypeError("CREDENTIAL_SCHEMA_INVALID", str(exc)) from None


async def create_type(session, type_key: str, name: str, schema: dict, *, enabled: bool, actor_id: str):
    if not TYPE_KEY.fullmatch(type_key) or type_key in LEGACY_TYPES:
        raise CredentialTypeError("CREDENTIAL_TYPE_INVALID", "类型标识无效或已保留")
    name = name.strip()
    if not 1 <= len(name) <= 128:
        raise CredentialTypeError("CREDENTIAL_TYPE_INVALID", "类型名称长度必须为 1–128")
    normalized = normalize_schema(schema)
    row = CredentialType(type_key=type_key, name=name, source="custom", enabled=enabled, created_by=actor_id)
    version = CredentialTypeVersion(
        type_key=type_key, version=1, schema_json=normalized, schema_hash=schema_digest(normalized), created_by=actor_id,
    )
    session.add_all([row, version])
    audit_change(session, "type_create", actor_id, {"type_key": type_key, "version": 1, "enabled": enabled})
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise CredentialTypeError("CREDENTIAL_TYPE_CONFLICT", "该凭据类型标识已存在", 409) from None
    return row, version


async def publish_version(session, type_key: str, schema: dict, *, expected_revision: int, actor_id: str):
    normalized = normalize_schema(schema)
    row = await get_type(session, type_key, for_update=True)
    _check_revision(row, expected_revision)
    if row.source != "custom":
        raise CredentialTypeError("CREDENTIAL_TYPE_BUILTIN", "内置类型版本由数据库迁移发布", 409)
    version_number = row.latest_version + 1
    version = CredentialTypeVersion(
        type_key=type_key, version=version_number, schema_json=normalized,
        schema_hash=schema_digest(normalized), created_by=actor_id,
    )
    session.add(version)
    row.latest_version = version_number
    row.revision += 1
    audit_change(session, "type_publish", actor_id, {"type_key": type_key, "version": version_number})
    await session.commit()
    return row, version


async def set_enabled(session, type_key: str, *, enabled: bool, expected_revision: int, actor_id: str):
    row = await get_type(session, type_key, for_update=True)
    _check_revision(row, expected_revision)
    if row.enabled != enabled:
        row.enabled = enabled
        row.revision += 1
        audit_change(session, "type_status", actor_id, {"type_key": type_key, "enabled": enabled})
    await session.commit()
    return row
