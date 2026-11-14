"""结构化凭据类型的门户接口。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin, require_api_key
from app.core.orm import get_db_session
from app.models.credential_type import CredentialType, CredentialTypeVersion
from app.services.secrets import type_catalog
from app.services.secrets.payload_config import creation_availability, load_payload_config

router = APIRouter()


class SchemaDefinition(BaseModel):
    schema_definition: dict[str, Any] = Field(alias="schema", repr=False)


class PublishSchema(SchemaDefinition):
    expected_revision: int = Field(ge=1)


class CreateType(SchemaDefinition):
    type_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class ChangeStatus(BaseModel):
    enabled: bool
    expected_revision: int = Field(ge=1)


def _summary(row: CredentialType, version: CredentialTypeVersion | None = None, *, structured_enabled: bool) -> dict:
    result = {
        "type_key": row.type_key, "name": row.name, "source": row.source, "enabled": row.enabled,
        "latest_version": row.latest_version, "revision": row.revision,
        **creation_availability(type_key=row.type_key, type_enabled=row.enabled, structured_enabled=structured_enabled),
    }
    if version is not None:
        result.update(version=version.version, schema=version.schema_json, schema_hash=version.schema_hash)
    return result


def _fail(error: type_catalog.CredentialTypeError) -> HTTPException:
    return HTTPException(error.status_code, detail={"code": error.code, "message": str(error)})


@router.get("")
async def list_types(
    include_disabled: bool = Query(False),
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    if include_disabled and user.get("role") != "admin":
        raise HTTPException(403, detail="需要管理员权限")
    stmt = select(CredentialType, CredentialTypeVersion).join(
        CredentialTypeVersion,
        (CredentialTypeVersion.type_key == CredentialType.type_key)
        & (CredentialTypeVersion.version == CredentialType.latest_version),
    ).order_by(CredentialType.source, CredentialType.type_key)
    if not include_disabled:
        stmt = stmt.where(CredentialType.enabled.is_(True))
    try:
        enabled = (await load_payload_config(db)).enabled
    except type_catalog.CredentialTypeError as exc:
        raise _fail(exc) from None
    return {"code": 0, "data": [_summary(row, version, structured_enabled=enabled) for row, version in await db.execute(stmt)]}


@router.get("/{type_key}/versions/{version}")
async def read_version(
    type_key: str, version: int,
    user: dict = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await type_catalog.get_type(db, type_key)
        schema = await type_catalog.get_version(db, type_key, version)
        enabled = (await load_payload_config(db)).enabled
    except type_catalog.CredentialTypeError as exc:
        raise _fail(exc) from None
    return {"code": 0, "data": _summary(row, schema, structured_enabled=enabled)}


@router.post("/validate")
async def check_schema(payload: SchemaDefinition, admin: dict = Depends(require_admin)):
    try:
        schema = type_catalog.normalize_schema(payload.schema_definition)
    except type_catalog.CredentialTypeError as exc:
        raise _fail(exc) from None
    return {"code": 0, "data": schema}


@router.post("")
async def create_type(
    payload: CreateType, admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db_session),
):
    try:
        row, version = await type_catalog.create_type(
            db, payload.type_key, payload.name, payload.schema_definition,
            enabled=payload.enabled, actor_id=str(admin["user_id"]),
        )
        enabled = (await load_payload_config(db)).enabled
    except type_catalog.CredentialTypeError as exc:
        raise _fail(exc) from None
    return {"code": 0, "data": _summary(row, version, structured_enabled=enabled)}


@router.post("/{type_key}/versions")
async def publish(
    type_key: str, payload: PublishSchema,
    admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db_session),
):
    try:
        row, version = await type_catalog.publish_version(
            db, type_key, payload.schema_definition, expected_revision=payload.expected_revision,
            actor_id=str(admin["user_id"]),
        )
        enabled = (await load_payload_config(db)).enabled
    except type_catalog.CredentialTypeError as exc:
        raise _fail(exc) from None
    return {"code": 0, "data": _summary(row, version, structured_enabled=enabled)}


@router.patch("/{type_key}/status")
async def set_status(
    type_key: str, payload: ChangeStatus,
    admin: dict = Depends(require_admin), db: AsyncSession = Depends(get_db_session),
):
    try:
        row = await type_catalog.set_enabled(
            db, type_key, enabled=payload.enabled, expected_revision=payload.expected_revision,
            actor_id=str(admin["user_id"]),
        )
        enabled = (await load_payload_config(db)).enabled
    except type_catalog.CredentialTypeError as exc:
        raise _fail(exc) from None
    return {"code": 0, "data": _summary(row, structured_enabled=enabled)}
