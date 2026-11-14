"""结构化载荷写入及可信出站解密。门户只读取元数据列。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.models.credential_type import StructuredCredential
from app.services.secrets.credential_schema import CredentialSchemaError, account_key, validate_payload
from app.services.secrets.payload_config import load_payload_config
from app.services.secrets.payload_crypto import PayloadCipher, PayloadCryptoError
from app.services.secrets.type_catalog import LEGACY_TYPES, CredentialTypeError, creation_version, get_version

STRUCTURED_FORMAT = "structured_v1"
LEGACY_FORMAT = "legacy"


@dataclass(repr=False, frozen=True)
class StructuredInput:
    type_key: str
    type_version: int
    account: str
    fields: dict[str, Any]
    extra_fields: dict[str, dict] | None = None


def _aad(row: StructuredCredential) -> list:
    return [
        row.secret_id, row.owner_scope, row.owner_id, row.type_key, row.type_version,
        row.account_digest, row.value_revision,
    ]


def _seal(row, prepared, cipher) -> None:
    row.key_id = cipher.active_id
    row.payload_encrypted = cipher.encrypt(prepared.values, aad=_aad(row))
    row.secret_fields_set = prepared.secret_fields
    row.extra_schema = prepared.extra_schema
    # 所有实例字段值均随载荷加密。管理接口只展示字段名和设置状态。
    row.public_projection = {}


async def create_payload(session, parent, data: StructuredInput) -> StructuredCredential:
    config = await load_payload_config(session)
    config.require_enabled()
    if data.type_key in LEGACY_TYPES:
        raise CredentialTypeError("CREDENTIAL_FORMAT_INVALID", "单值凭据和变量使用原有值字段")
    version = await creation_version(session, data.type_key, data.type_version)
    try:
        account, digest = account_key(data.account)
        prepared = validate_payload(version.schema_json, data.fields, data.extra_fields)
        row = StructuredCredential(
            secret_id=parent.id, owner_scope=parent.owner_scope, owner_id=parent.owner_id,
            type_key=version.type_key, type_version=version.version, account=account,
            account_digest=digest, value_revision=1,
        )
        _seal(row, prepared, config.cipher())
    except CredentialSchemaError as exc:
        raise CredentialTypeError("CREDENTIAL_FIELDS_INVALID", str(exc)) from None
    except PayloadCryptoError as exc:
        raise CredentialTypeError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", str(exc), 503) from None
    session.add(row)
    return row


def decrypt_fields(row: StructuredCredential, cipher: PayloadCipher) -> dict:
    if row.payload_encrypted.partition(".")[0] != row.key_id:
        raise PayloadCryptoError("结构化凭据无法解密")
    return cipher.decrypt(row.payload_encrypted, aad=_aad(row))


async def rotate_payload(session, parent, changes: dict, expected_revision: int | None) -> StructuredCredential:
    config = await load_payload_config(session)
    row = await session.get(StructuredCredential, parent.id)
    if row is None or (row.owner_scope, row.owner_id) != (parent.owner_scope, parent.owner_id):
        raise CredentialTypeError("CREDENTIAL_PAYLOAD_UNAVAILABLE", "结构化凭据不可用", 409)
    if expected_revision != row.value_revision:
        raise CredentialTypeError("CREDENTIAL_REVISION_CONFLICT", "数据已更新，请刷新后重试", 409)
    if not changes:
        raise CredentialTypeError("CREDENTIAL_FIELDS_INVALID", "请填写要轮换的字段")
    version = await get_version(session, row.type_key, row.type_version)
    try:
        cipher = config.cipher()
        fields = decrypt_fields(row, cipher)
        prepared = validate_payload(version.schema_json, {**fields, **changes}, row.extra_schema)
        row.value_revision += 1
        _seal(row, prepared, cipher)
    except CredentialSchemaError as exc:
        raise CredentialTypeError("CREDENTIAL_FIELDS_INVALID", str(exc)) from None
    except PayloadCryptoError as exc:
        raise CredentialTypeError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", str(exc), 503) from None
    return row


async def describe_payloads(session, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    model = StructuredCredential
    stmt = select(
        model.secret_id, model.type_key, model.type_version, model.account, model.value_revision,
        model.secret_fields_set, model.extra_schema,
    ).where(model.secret_id.in_(ids))
    return {
        row.secret_id: {
            "type_key": row.type_key, "type_version": row.type_version, "account": row.account,
            "value_revision": row.value_revision, "secret_fields_set": row.secret_fields_set,
            "extra_fields": row.extra_schema,
        }
        for row in await session.execute(stmt)
    }
