import base64
import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.portal.endpoints import system
from app.core import orm
from app.core.config import settings
from app.core.dependencies import require_api_key
from app.core.middleware import AccessLogMiddleware
from app.services import config_service
from app.services.audit_service import AuditService
from app.services.secrets.payload_crypto import PayloadCryptoError
from app.services.secrets.type_catalog import CredentialTypeError
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401


ENABLED = "structured_credentials_enabled"
KEYRING = "credential_payload_keyring_json"
PATH = "/api/portal/system/configs/structured-credentials"
OLD_KEY = base64.urlsafe_b64encode(b"A" * 32).decode()
NEW_KEY = base64.urlsafe_b64encode(b"B" * 32).decode()


def ring(active_id, keys):
    return json.dumps({"active_key_id": active_id, "keys": keys})


@pytest_asyncio.fixture
async def config_db(monkeypatch):
    table = config_service._SYSTEM_CONFIGS_TABLE
    async with temporary_database(table) as engine:
        async with engine.begin() as connection:
            await connection.execute(insert(table), [
                {"key": ENABLED, "value": None, "category": "security", "is_secret": False},
                {"key": KEYRING, "value": None, "category": "security", "is_secret": True},
            ])
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(orm, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(config_service, "AsyncSessionLocal", sessions)
        monkeypatch.setattr(config_service.ConfigService, "_all_configs_cache", None)
        monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr("{}"))
        monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "")
        yield sessions


@pytest.mark.asyncio
async def test_switch_and_keyring_save_as_plaintext_and_hide_from_read_paths(config_db, monkeypatch):
    app = FastAPI()
    app.include_router(system.router, prefix="/api/portal/system")
    app.dependency_overrides[require_api_key] = lambda: {"role": "admin", "user_name": "fixture"}
    app.add_middleware(AccessLogMiddleware)
    audit = []

    async def capture(**event):
        audit.append(event)

    monkeypatch.setattr(AuditService, "log_request_data", capture)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(PATH, json={ENABLED: True, KEYRING: ring("v1", {"v1": OLD_KEY})})
        assert response.status_code == 200
        assert response.json() == {ENABLED: True, "keyring_status": "valid", "active_key_id": "v1"}
        listed = await client.get("/api/portal/system/configs")
        assert listed.status_code == 200
        assert OLD_KEY not in listed.text
        assert KEYRING in {item["key"] for item in listed.json()["security"]}
        history = await client.get(f"/api/portal/system/configs/{KEYRING}/history")
        assert history.json() == []
        assert audit[0]["request_params"] is None and audit[0]["response_body"] is None
        app.dependency_overrides[require_api_key] = lambda: {"role": "user"}
        assert (await client.put(PATH, json={ENABLED: False})).status_code == 403

    table = config_service._SYSTEM_CONFIGS_TABLE
    async with config_db() as session:
        stored = dict((await session.execute(select(table.c.key, table.c.value))).all())
    assert stored[ENABLED] == "true"
    assert json.loads(stored[KEYRING]) == {"active_key_id": "v1", "keys": {"v1": OLD_KEY}}
    assert KEYRING not in await config_service.ConfigService.get_all_from_db()


@pytest.mark.asyncio
async def test_rotation_recovers_old_read_keys_from_incomplete_deployment(config_db, monkeypatch):
    from app.services.secrets import payload_config

    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr(json.dumps({"v1": OLD_KEY})))
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "missing")
    assert (await payload_config.load_payload_config()).status()["keyring_status"] == "invalid"

    await payload_config.save_payload_config(keyring_json=ring("v2", {"v2": NEW_KEY}))
    current = await payload_config.load_payload_config()
    assert json.loads(current.raw_keyring)["keys"] == {"v1": OLD_KEY, "v2": NEW_KEY}


@pytest.mark.asyncio
async def test_rotation_keeps_deployment_and_previous_keys(config_db, monkeypatch):
    from app.services.secrets import payload_config

    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_KEYRING_JSON", SecretStr(json.dumps({"v1": OLD_KEY})))
    monkeypatch.setattr(settings, "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID", "v1")
    legacy = await payload_config.load_payload_config()
    legacy_cipher = legacy.cipher()
    old_envelope = legacy_cipher.encrypt({"password": "mail-fixture"}, aad=["fixture"])

    await payload_config.save_payload_config(keyring_json=ring("v2", {"v2": NEW_KEY}))
    current = (await payload_config.load_payload_config()).cipher()
    assert current.active_id == "v2"
    assert current.decrypt(old_envelope, aad=["fixture"]) == {"password": "mail-fixture"}

    with pytest.raises(ValueError, match="更换密钥编号"):
        await payload_config.save_payload_config(keyring_json=ring("v1", {"v1": NEW_KEY}))
    assert (await payload_config.load_payload_config()).cipher().active_id == "v2"

    await payload_config.save_payload_config(enabled=False)
    disabled = await payload_config.load_payload_config()
    assert disabled.configured
    assert disabled.cipher().decrypt(old_envelope, aad=["fixture"]) == {"password": "mail-fixture"}
    with pytest.raises(CredentialTypeError) as rejected:
        disabled.require_enabled()
    assert rejected.value.code == "STRUCTURED_CREDENTIALS_DISABLED"


@pytest.mark.asyncio
async def test_invalid_keyring_cannot_enable_feature_or_echo_the_value(config_db):
    from app.services.secrets import payload_config

    with pytest.raises(PayloadCryptoError) as rejected:
        await payload_config.save_payload_config(enabled=True, keyring_json=ring("missing", {"v1": OLD_KEY}))
    assert OLD_KEY not in str(rejected.value)
    assert (await payload_config.load_payload_config()).status() == {
        ENABLED: False, "keyring_status": "unconfigured", "active_key_id": None,
    }


def test_generic_settings_do_not_accept_managed_keys():
    for key in (ENABLED, KEYRING, KEYRING.upper()):
        with pytest.raises(ValueError, match="凭证"):
            config_service.validate_config_update(key, "value")
