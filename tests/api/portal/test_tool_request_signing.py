"""API 工具请求签名：端点装配、试跑密钥解析与只写语义。

大部分用例直接调用端点函数并使用内存假 DB，覆盖「密钥不落明文、不回显、
失败关闭」的装配逻辑；最后一个用例走真实库，验证创建/更新链路与迁移后的列兼容。
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest

from app.api.portal.endpoints.tools import (
    _apply_signing_for_update,
    _prepare_signing_for_create,
    _resolve_preview_signing,
    create_tool,
    preview_tool,
    update_tool,
)
from app.models.tool import SysApiTool
from app.schemas.tool import (
    SysApiToolCreate,
    SysApiToolPreviewRequest,
    SysApiToolResponse,
    SysApiToolUpdate,
)
from app.services.ai.tools.request_signing import SigningConfigError

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
KEY_ID = "8f3c2a1b7d9e4c60"


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return [] if self.value is None else [self.value]


class FakeDB:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.added = []

    async def execute(self, _stmt):
        return FakeResult(self.results.pop(0) if self.results else None)

    async def get(self, _model, _identity):
        return self.results.pop(0) if self.results else None

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, value):
        if getattr(value, "created_at", None) is None:
            value.created_at = datetime.now()
        if getattr(value, "updated_at", None) is None:
            value.updated_at = datetime.now()


def _tool(**overrides):
    values = {
        "id": "tool-1",
        "name": "signed-tool",
        "method": "POST",
        "url_template": "https://api.test/refund",
        "headers": "{}",
        "parameter_schema": "{}",
        "signing_enabled": True,
        "signing_key_id": KEY_ID,
        "signing_algorithm": "hmac-sha256",
        "signing_secret_encrypted": None,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    values.update(overrides)
    return SysApiTool(**values)


def test_create_encrypts_secret_and_never_keeps_plaintext():
    payload = SysApiToolCreate(
        name="signed-tool",
        method="POST",
        url_template="https://api.test/refund",
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_secret=SECRET,
    )

    data = _prepare_signing_for_create(payload)

    assert "signing_secret" not in data
    assert data["signing_key_id"] == KEY_ID
    assert data["signing_secret_encrypted"].startswith("sig:v1:")
    assert SECRET not in data["signing_secret_encrypted"]


def test_create_rejects_enabled_signing_without_secret():
    payload = SysApiToolCreate(
        name="signed-tool",
        method="POST",
        url_template="https://api.test/refund",
        signing_enabled=True,
        signing_key_id=KEY_ID,
    )

    with pytest.raises(SigningConfigError, match="密钥"):
        _prepare_signing_for_create(payload)


def test_create_ignores_secret_when_signing_disabled():
    payload = SysApiToolCreate(
        name="signed-tool",
        method="POST",
        url_template="https://api.test/refund",
        signing_enabled=False,
        signing_secret=SECRET,
    )

    data = _prepare_signing_for_create(payload)
    assert data["signing_key_id"] is None
    assert data["signing_secret_encrypted"] is None
    assert "signing_secret" not in data


def test_create_rejects_reserved_header_prefix():
    payload = SysApiToolCreate(
        name="signed-tool",
        method="POST",
        url_template="https://api.test/refund",
        headers={"x-nanzi-signature": "forged"},
    )

    with pytest.raises(SigningConfigError, match="保留前缀"):
        _prepare_signing_for_create(payload)


def test_update_enable_uses_existing_secret():
    tool = _tool(signing_enabled=False, signing_key_id=KEY_ID, signing_secret_encrypted="sig:v1:stored")
    data = _apply_signing_for_update(tool, SysApiToolUpdate(signing_enabled=True))
    assert data == {"signing_enabled": True}


def test_update_rotation_requires_secret_and_key_id_together():
    tool = _tool(signing_enabled=True, signing_secret_encrypted="sig:v1:stored")

    with pytest.raises(SigningConfigError, match="同时提供"):
        _apply_signing_for_update(tool, SysApiToolUpdate(signing_secret=SECRET))
    with pytest.raises(SigningConfigError, match="同时提供"):
        _apply_signing_for_update(tool, SysApiToolUpdate(signing_key_id="abcdef0123456789"))


def test_update_rotation_rejects_reusing_the_same_key_id():
    tool = _tool(signing_enabled=True, signing_secret_encrypted="sig:v1:old")
    with pytest.raises(SigningConfigError, match="新的 Key-Id"):
        _apply_signing_for_update(
            tool,
            SysApiToolUpdate(signing_key_id=KEY_ID, signing_secret=SECRET),
        )


def test_update_rotation_encrypts_new_secret():
    tool = _tool(signing_enabled=True, signing_secret_encrypted="sig:v1:old")
    data = _apply_signing_for_update(
        tool,
        SysApiToolUpdate(signing_key_id="abcdef0123456789", signing_secret=SECRET),
    )
    assert data["signing_key_id"] == "abcdef0123456789"
    assert data["signing_secret_encrypted"].startswith("sig:v1:")
    assert SECRET not in data["signing_secret_encrypted"]


@pytest.mark.asyncio
async def test_preview_uses_draft_secret_without_storing_it():
    payload = SysApiToolPreviewRequest(
        method="POST",
        url_template="https://api.test/refund",
        params={"amount": 1},
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_secret=SECRET,
    )

    fields = await _resolve_preview_signing(payload, FakeDB())

    assert fields["signing_enabled"] is True
    assert fields["signing_key_id"] == KEY_ID
    assert fields["signing_secret_encrypted"].startswith("sig:v1:")
    assert SECRET not in fields["signing_secret_encrypted"]


@pytest.mark.asyncio
async def test_preview_uses_saved_tool_secret_by_tool_id():
    tool = _tool(signing_secret_encrypted="sig:v1:saved")
    payload = SysApiToolPreviewRequest(
        method="POST",
        url_template="https://api.test/refund",
        params={"amount": 1},
        signing_enabled=True,
        tool_id=tool.id,
    )

    fields = await _resolve_preview_signing(payload, FakeDB([tool]))

    assert fields["signing_secret_encrypted"] == "sig:v1:saved"
    assert fields["signing_key_id"] == KEY_ID


@pytest.mark.asyncio
async def test_preview_rejects_saved_tool_key_id_mismatch():
    tool = _tool(signing_secret_encrypted="sig:v1:saved")
    payload = SysApiToolPreviewRequest(
        method="POST",
        url_template="https://api.test/refund",
        params={"amount": 1},
        signing_enabled=True,
        tool_id=tool.id,
        signing_key_id="abcdef0123456789",
    )

    with pytest.raises(SigningConfigError, match="不一致"):
        await _resolve_preview_signing(payload, FakeDB([tool]))


@pytest.mark.asyncio
async def test_preview_endpoint_returns_invalid_signing_config_before_request():
    payload = SysApiToolPreviewRequest(
        method="POST",
        url_template="https://api.test/refund",
        params={"amount": 1},
        signing_enabled=True,
        signing_key_id=KEY_ID,
    )
    response = await preview_tool(payload, db=FakeDB(), user={"user_id": 1})
    body = response.model_dump()
    assert body["ok"] is False
    assert body["error_kind"] == "invalid_signing_config"


@pytest.mark.asyncio
async def test_preview_endpoint_passes_signing_config_to_kernel(monkeypatch):
    captured = {}

    async def fake_perform(config, params, **kwargs):
        captured["config"] = config
        captured["capture"] = kwargs.get("capture")
        kwargs["capture"].update({
            "method": "POST",
            "url": "https://api.test/refund",
            "headers": {"X-Nanzi-Signature": "sig"},
            "query": {},
            "body": params,
        })
        return httpx.Response(200, json={"ok": True}), 3, config.url_template

    monkeypatch.setattr(
        "app.api.portal.endpoints.tools.GenericApiToolFactory._perform_request",
        fake_perform,
    )
    payload = SysApiToolPreviewRequest(
        method="POST",
        url_template="https://api.test/refund",
        params={"amount": 1},
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_secret=SECRET,
    )
    response = await preview_tool(payload, db=FakeDB(), user={"user_id": 1})
    body = response.model_dump()

    assert body["ok"] is True
    assert captured["config"].signing_enabled is True
    assert captured["config"].signing_key_id == KEY_ID
    assert body["request"]["headers"]["X-Nanzi-Signature"] == "sig"


@pytest.mark.asyncio
async def test_create_tool_endpoint_does_not_return_plaintext_secret():
    db = FakeDB([None, None])
    created = await create_tool(
        SysApiToolCreate(
            name="signed-create",
            method="POST",
            url_template="https://api.test/refund",
            signing_enabled=True,
            signing_key_id=KEY_ID,
            signing_secret=SECRET,
        ),
        db=db,
        user={"user_id": 1},
    )

    assert created.signing_secret_configured is True
    assert created.signing_secret_encrypted.startswith("sig:v1:")
    assert SECRET not in created.signing_secret_encrypted
    assert len(db.added) == 1

    response = SysApiToolResponse.model_validate(created)
    assert response.signing_secret_configured is True
    assert "signing_secret" not in response.model_dump()


@pytest.mark.asyncio
async def test_update_tool_endpoint_rejects_key_id_collision():
    tool = _tool(signing_enabled=True, signing_secret_encrypted="sig:v1:stored")
    other = _tool(id="tool-2", name="other", signing_key_id="abcdef0123456789")
    db = FakeDB([tool, other])

    with pytest.raises(Exception) as exc_info:
        await update_tool(
            tool.id,
            SysApiToolUpdate(signing_key_id="abcdef0123456789", signing_secret=SECRET),
            db=db,
            user={"user_id": 1},
        )

    assert getattr(exc_info.value, "status_code", None) == 400
    assert "Key-Id" in getattr(exc_info.value, "detail", "")


def test_update_keeps_existing_secret_when_same_key_id_is_resubmitted():
    tool = _tool(signing_enabled=True, signing_secret_encrypted="sig:v1:stored")
    data = _apply_signing_for_update(
        tool,
        SysApiToolUpdate(signing_enabled=True, signing_key_id=KEY_ID),
    )
    assert data["signing_key_id"] == KEY_ID
    assert "signing_secret_encrypted" not in data


def test_update_rejects_changing_key_id_without_new_secret():
    tool = _tool(signing_enabled=True, signing_secret_encrypted="sig:v1:stored")
    with pytest.raises(SigningConfigError, match="同时提供"):
        _apply_signing_for_update(
            tool,
            SysApiToolUpdate(signing_key_id="abcdef0123456789"),
        )


@pytest.mark.asyncio
async def test_create_tool_endpoint_still_returns_400_for_invalid_system_params():
    from fastapi import HTTPException

    db = FakeDB([None])
    with pytest.raises(HTTPException) as exc_info:
        await create_tool(
            SysApiToolCreate(
                name="bad-system-param",
                method="POST",
                url_template="https://api.test/refund",
                parameter_schema={"x-nanzi-system-params": [{"name": "applicant", "value": ""}]},
            ),
            db=db,
            user={"user_id": 1},
        )

    assert exc_info.value.status_code == 400
    assert "applicant" in exc_info.value.detail


@pytest.mark.asyncio
async def test_preview_endpoint_uses_the_same_signing_kernel():
    from unittest.mock import AsyncMock, patch

    payload = SysApiToolPreviewRequest(
        method="POST",
        url_template="https://api.test/refund?source=preview",
        headers={"X-Business": "demo"},
        params={"amount": 1},
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_secret=SECRET,
    )
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        request.return_value = httpx.Response(200, json={"ok": True})
        response = await preview_tool(
            payload,
            db=FakeDB(),
            user={"user_id": 1, "user_name": "alice"},
        )
        args, kwargs = request.call_args

    body = response.model_dump()
    assert body["ok"] is True
    headers = {key.lower(): value for key, value in body["request"]["headers"].items()}
    assert headers["x-nanzi-signature-version"] == "1"
    assert headers["x-nanzi-key-id"] == KEY_ID
    assert headers["x-nanzi-signature"]
    assert args[0] == "POST"
    assert "source=preview" in args[1]


@pytest.mark.asyncio
async def test_signed_tool_can_be_created_and_updated_without_resubmitting_secret(
    client, admin_api_key,
):
    """真实库端到端：创建时加密保存，更新同一 Key-Id 不重传密钥也不报错。"""
    key_id = secrets.token_hex(8)
    secret = secrets.token_hex(32)
    name = f"signed-e2e-{secrets.token_hex(4)}"
    headers = {"X-API-Key": admin_api_key}
    tool_id = None
    try:
        create_response = await client.post(
            "/api/portal/tools",
            json={
                "name": name,
                "description": "signing e2e",
                "method": "POST",
                "url_template": "https://api.test/refund",
                "headers": {},
                "parameter_schema": {"properties": {"amount": {"type": "number"}}},
                "signing_enabled": True,
                "signing_key_id": key_id,
                "signing_secret": secret,
            },
            headers=headers,
        )
        assert create_response.status_code == 200, create_response.text
        created = create_response.json()
        tool_id = created["id"]
        assert created["signing_enabled"] is True
        assert created["signing_secret_configured"] is True
        assert "signing_secret" not in created

        update_response = await client.put(
            f"/api/portal/tools/{tool_id}",
            json={
                "description": "signing e2e updated",
                "signing_enabled": True,
                "signing_key_id": key_id,
            },
            headers=headers,
        )
        assert update_response.status_code == 200, update_response.text
        updated = update_response.json()
        assert updated["signing_secret_configured"] is True
        assert "signing_secret" not in updated
    finally:
        if tool_id:
            await client.delete(f"/api/portal/tools/{tool_id}", headers=headers)
