"""系统级参数在工具保存/试跑接口上的契约。"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest

from app.services.ai.tools.generic_api import GenericApiToolFactory

PREVIEW_URL = "/api/portal/tools/preview"
TOOLS_URL = "/api/portal/tools"


def _preview_payload(**overrides):
    payload = {
        "method": "POST",
        "url_template": "https://api.test/refund",
        "headers": {},
        "parameter_schema": {
            "properties": {"amount": {"type": "number"}},
            "x-nanzi-system-params": [
                {"name": "applicant", "type": "string", "value": "{user.user_name}"}
            ],
        },
        "params": {"amount": 100},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_preview_rejects_invalid_system_param_config(client, admin_api_key):
    response = await client.post(
        PREVIEW_URL,
        json=_preview_payload(
            parameter_schema={
                "x-nanzi-system-params": [
                    {"name": "applicant", "value": ""}
                ]
            }
        ),
        headers={"X-API-Key": admin_api_key},
    )

    body = response.json()
    assert body["ok"] is False
    assert body["error_kind"] == "invalid_parameter_schema"
    assert "applicant" in body["message"]


@pytest.mark.asyncio
async def test_create_rejects_invalid_system_param_config(client, admin_api_key):
    response = await client.post(
        TOOLS_URL,
        json={
            "name": f"invalid-system-param-{uuid.uuid4().hex[:8]}",
            "method": "POST",
            "url_template": "https://api.test/refund",
            "headers": {},
            "parameter_schema": {
                "x-nanzi-system-params": [
                    {"name": "applicant", "value": ""}
                ]
            },
        },
        headers={"X-API-Key": admin_api_key},
    )

    assert response.status_code == 400
    assert "applicant" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_returns_the_actual_request_with_injected_system_param(
    client, admin_api_key,
):
    real_request = httpx.AsyncClient.request

    async def fake_request(self, method, url, **kwargs):
        # 只拦截试跑目标，测试客户端自己打 /preview 仍走 ASGITransport
        if str(url).startswith("https://api.test"):
            return httpx.Response(200, json={"ok": True})
        return await real_request(self, method, url, **kwargs)

    with patch("httpx.AsyncClient.request", new=fake_request):
        response = await client.post(
            PREVIEW_URL,
            json=_preview_payload(),
            headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is True, body
    assert "request" in body, body
    assert body["request"]["method"] == "POST"
    assert body["request"]["url"] == "https://api.test/refund"
    assert body["request"]["body"] == {"amount": 100, "applicant": "test_admin"}
    assert body["request"]["query"] == {}


@pytest.mark.asyncio
async def test_preview_masks_credentials_in_request_details(client, admin_api_key):
    real_request = httpx.AsyncClient.request

    async def fake_request(self, method, url, **kwargs):
        if str(url).startswith("https://api.test"):
            return httpx.Response(200, json={"ok": True})
        return await real_request(self, method, url, **kwargs)

    with patch("httpx.AsyncClient.request", new=fake_request):
        response = await client.post(
            PREVIEW_URL,
            json=_preview_payload(
                method="GET",
                url_template="https://api.test/refund",
                headers={"Authorization": "Bearer top-secret"},
                params={"amount": 100, "token": "super-secret"},
            ),
            headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is True, body
    assert body["request"]["headers"]["Authorization"] == "******"
    assert "super-secret" not in body["request"]["url"]
    assert "token=******" in body["request"]["url"]


@pytest.mark.asyncio
async def test_preview_does_not_require_system_param_in_url(client, admin_api_key):
    captured = {}

    async def fake(config, params, **kwargs):
        captured["params"] = params
        return (
            httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("POST", "https://api.test"),
            ),
            3,
            config.url_template,
        )

    with patch.object(GenericApiToolFactory, "_perform_request", new=fake):
        response = await client.post(
            PREVIEW_URL,
            json=_preview_payload(url_template="https://api.test/users/{applicant}"),
            headers={"X-API-Key": admin_api_key},
        )

    body = response.json()
    assert body["ok"] is True, body
    assert captured["params"] == {"amount": 100}
