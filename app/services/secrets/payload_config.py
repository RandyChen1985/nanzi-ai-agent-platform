"""管理结构化凭据开关与只写密钥环。"""

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import orm
from app.core.config import settings
from app.services.config_service import _SYSTEM_CONFIGS_TABLE
from app.services.secrets.payload_crypto import (
    ENABLED_CONFIG_KEY,
    KEYRING_CONFIG_KEY,
    PAYLOAD_CONFIG_KEYS,
    PayloadCipher,
    PayloadCryptoError,
    deployment_keyring_configured,
    parse_payload_keyring,
    previous_payload_keys,
)
from app.services.secrets.type_catalog import LEGACY_TYPES, CredentialTypeError


CREATION_DISABLED_REASON = "管理员已关闭结构化凭据新建，已有凭据仍可使用和维护"


def creation_availability(*, type_key: str, type_enabled: bool, structured_enabled: bool) -> dict:
    reason = None
    if not type_enabled:
        reason = "此凭据类型已停用新建，已有凭据仍可使用和维护"
    elif type_key not in LEGACY_TYPES and not structured_enabled:
        reason = CREATION_DISABLED_REASON
    return {"can_create": reason is None, "creation_disabled_reason": reason}


@dataclass(frozen=True, repr=False)
class PayloadConfig:
    enabled: bool
    raw_keyring: str
    legacy_active_id: str | None = None

    def parse_keyring(self) -> PayloadCipher:
        return parse_payload_keyring(self.raw_keyring, self.legacy_active_id)

    @property
    def configured(self) -> bool:
        return bool(self.legacy_active_id) or self.raw_keyring.strip() not in {"", "{}"}

    def require_enabled(self) -> None:
        """仅供新建实例校验；已有实例的读写只校验密钥环。"""
        if not self.enabled:
            raise CredentialTypeError("STRUCTURED_CREDENTIALS_DISABLED", CREATION_DISABLED_REASON, 503)

    def cipher(self) -> PayloadCipher:
        return self.parse_keyring()

    def status(self) -> dict:
        status = "unconfigured"
        active_id = None
        if self.configured:
            try:
                active_id = self.parse_keyring().active_id
                status = "valid"
            except PayloadCryptoError:
                status = "invalid"
        return {ENABLED_CONFIG_KEY: self.enabled, "keyring_status": status, "active_key_id": active_id}


def _effective_config(values: dict[str, str | None]) -> PayloadConfig:
    ring = values.get(KEYRING_CONFIG_KEY)
    flag = values.get(ENABLED_CONFIG_KEY)
    # 管理员首次保存前沿用已有的部署配置，避免升级时停掉既有任务。
    enabled = deployment_keyring_configured() if flag is None else flag == "true"
    if ring is not None:
        return PayloadConfig(enabled, ring)
    return PayloadConfig(
        enabled,
        settings.CREDENTIAL_PAYLOAD_KEYRING_JSON.get_secret_value(),
        settings.CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID or None,
    )


def _config_query():
    table = _SYSTEM_CONFIGS_TABLE
    return select(table.c.key, table.c.value).where(table.c.key.in_(PAYLOAD_CONFIG_KEYS)).order_by(table.c.key)


async def load_payload_config(session: AsyncSession | None = None) -> PayloadConfig:
    if session is None:
        async with orm.AsyncSessionLocal() as db:
            return await load_payload_config(db)
    try:
        return _effective_config(dict((await session.execute(_config_query())).all()))
    except SQLAlchemyError:
        raise CredentialTypeError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", "结构化凭据配置暂不可用", 503) from None


async def payload_keyring_requires_isolation(session: AsyncSession | None = None) -> bool:
    if deployment_keyring_configured():
        return True
    if session is None:
        async with orm.AsyncSessionLocal() as db:
            return await payload_keyring_requires_isolation(db)
    table = _SYSTEM_CONFIGS_TABLE
    try:
        present = await session.scalar(
            select(table.c.key).where(table.c.key == KEYRING_CONFIG_KEY, table.c.value.is_not(None))
        )
        return present is not None
    except SQLAlchemyError:
        # 无法确认密钥是否存在时，继续执行原有的隔离保护。
        return True


async def save_payload_config(*, enabled: bool | None = None, keyring_json: str | None = None) -> dict:
    incoming = parse_payload_keyring(keyring_json) if keyring_json is not None else None
    if enabled is None and incoming is None:
        raise ValueError("请填写要保存的结构化凭证配置")

    table = _SYSTEM_CONFIGS_TABLE
    async with orm.AsyncSessionLocal() as session:
        # 迁移预置两行。按固定顺序加锁，避免同时轮换时覆盖另一位管理员刚写入的密钥。
        values = dict((await session.execute(_config_query().with_for_update())).all())
        if not PAYLOAD_CONFIG_KEYS.issubset(values):
            raise CredentialTypeError("CREDENTIAL_ENCRYPTION_UNAVAILABLE", "请先应用结构化凭证配置数据库迁移", 503)
        current = _effective_config(values)
        if enabled is None:
            enabled = current.enabled

        if incoming is not None:
            previous = previous_payload_keys(current.raw_keyring, legacy=current.legacy_active_id is not None)
            if values[KEYRING_CONFIG_KEY] is None and current.legacy_active_id is None:
                previous.update(previous_payload_keys(current.raw_keyring, legacy=True))
            raw_keyring = incoming.serialize_with_previous(previous)
            if len(raw_keyring.encode("ascii")) > 65535:
                raise ValueError("结构化凭据密钥环超出存储长度")
            await session.execute(update(table).where(table.c.key == KEYRING_CONFIG_KEY).values(value=raw_keyring))
            current = PayloadConfig(enabled, raw_keyring)
        else:
            current = PayloadConfig(enabled, current.raw_keyring, current.legacy_active_id)

        if enabled:
            current.parse_keyring()
        await session.execute(update(table).where(table.c.key == ENABLED_CONFIG_KEY).values(value=str(enabled).lower()))
        await session.commit()
    return current.status()
