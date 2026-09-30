"""Langfuse 配置接口契约测试。

数据库交互被替换成桩：这一层只验证 HTTP 契约（鉴权、掩码、校验失败映射、错误可读性）。
真实落库往返由 ``tests/services/ai/observability/test_config_store_db.py`` 覆盖
（该文件在未应用迁移的环境下会跳过）。
"""

import pytest
from httpx import AsyncClient

from app.services.ai.observability import config_store, manager, settings

BASE = "/api/portal/system/langfuse"


@pytest.fixture(autouse=True)
def _hermetic_observability(monkeypatch):
    """隔离追踪运行态，避免污染同进程其它用例。

    两个必须做的事：
    1. 每个用例前后复位快照与 client 引用（快照是进程级全局状态）；
    2. 绝不在接口测试里构造真实 Langfuse client —— 构造它会注册**全局**
       TracerProvider，从而影响同进程后续所有用例。
    """
    settings.reset_for_tests()
    manager.reset_for_tests()
    monkeypatch.setattr(manager, "_build_client", lambda _snapshot: object())
    yield
    settings.reset_for_tests()
    manager.reset_for_tests()


def _stub_load(monkeypatch, **overrides):
    row = {
        "enabled": True,
        "host": "http://lf:3000",
        "public_key": "pk-lf-1",
        "secret_key": "sk-lf-secret",
        "sample_rate": 0.5,
        "capture_content": False,
        "environment": "prod",
        "release": "2026.09",
        "timeout_seconds": 9,
        "trace_url_template": "http://lf:3000/trace/{trace_id}",
        "updated_by": "tester",
    }
    row.update(overrides)

    async def fake_load():
        return row

    monkeypatch.setattr(config_store, "load_config", fake_load)
    return row


@pytest.mark.asyncio
async def test_requires_api_key(client: AsyncClient):
    response = await client.get(BASE)
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_get_returns_config_without_secret(client: AsyncClient, admin_api_key: str,
                                                 monkeypatch):
    _stub_load(monkeypatch)

    response = await client.get(BASE, headers={"X-API-Key": admin_api_key})

    assert response.status_code == 200
    data = response.json()
    assert data["host"] == "http://lf:3000"
    assert data["public_key"] == "pk-lf-1"
    assert data["sample_rate"] == 0.5
    assert data["capture_content"] is False
    assert data["environment"] == "prod"
    assert data["timeout_seconds"] == 9
    assert data["trace_url_template"] == "http://lf:3000/trace/{trace_id}"
    assert data["updated_by"] == "tester"
    # 密钥绝不回传明文
    assert "secret_key" not in data
    assert "sk-lf-secret" not in response.text
    assert data["has_secret_key"] is True


@pytest.mark.asyncio
async def test_get_reports_missing_secret(client: AsyncClient, admin_api_key: str, monkeypatch):
    _stub_load(monkeypatch, secret_key=None)

    response = await client.get(BASE, headers={"X-API-Key": admin_api_key})

    assert response.json()["has_secret_key"] is False


@pytest.mark.asyncio
async def test_get_exposes_runtime_status(client: AsyncClient, admin_api_key: str, monkeypatch):
    _stub_load(monkeypatch)

    response = await client.get(BASE, headers={"X-API-Key": admin_api_key})

    data = response.json()
    assert "status" in data
    assert "status_label" in data
    assert "client_active" in data


@pytest.mark.asyncio
async def test_put_rejects_invalid_payload(client: AsyncClient, admin_api_key: str):
    response = await client.put(
        BASE,
        json={"sample_rate": 9},
        headers={"X-API-Key": admin_api_key},
    )

    assert response.status_code == 400
    # 全局异常处理器把错误包装成 {code, message, detail...}，detail 为空、原因在 message。
    assert "sample_rate" in response.json()["message"]


@pytest.mark.asyncio
async def test_put_rejects_invalid_host(client: AsyncClient, admin_api_key: str):
    response = await client.put(
        BASE,
        json={"host": "lf:3000"},
        headers={"X-API-Key": admin_api_key},
    )

    assert response.status_code == 400
    assert "host" in response.json()["message"]


@pytest.mark.asyncio
async def test_put_saves_and_returns_public_view(client: AsyncClient, admin_api_key: str,
                                                 monkeypatch):
    captured = {}

    async def fake_save(payload, changed_by=None):
        captured["payload"] = payload
        captured["changed_by"] = changed_by
        return {
            "enabled": True,
            "host": "http://lf:3000",
            "public_key": "pk",
            "secret_key": "sk-lf-new",
            "sample_rate": 1.0,
            "capture_content": True,
            "environment": None,
            "release": None,
            "timeout_seconds": 5,
            "trace_url_template": None,
            "updated_by": changed_by,
        }

    monkeypatch.setattr(config_store, "save_config", fake_save)
    _stub_load(monkeypatch, secret_key="sk-lf-new")

    response = await client.put(
        BASE,
        json={"enabled": True, "host": "http://lf:3000", "secret_key": "sk-lf-new"},
        headers={"X-API-Key": admin_api_key},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert captured["payload"]["secret_key"] == "sk-lf-new"
    assert captured["changed_by"]
    assert "secret_key" not in body["data"]
    assert body["data"]["has_secret_key"] is True


@pytest.mark.asyncio
async def test_put_refreshes_runtime_snapshot(client: AsyncClient, admin_api_key: str,
                                             monkeypatch):
    refreshed = {"count": 0}

    async def fake_save(payload, changed_by=None):
        return {
            "enabled": False, "host": None, "public_key": None, "secret_key": None,
            "sample_rate": 1.0, "capture_content": True, "environment": None,
            "release": None, "timeout_seconds": 5, "trace_url_template": None,
            "updated_by": changed_by,
        }

    async def fake_refresh():
        refreshed["count"] += 1

    monkeypatch.setattr(config_store, "save_config", fake_save)
    _stub_load(monkeypatch, enabled=False)
    monkeypatch.setattr(
        "app.services.ai.observability.settings.refresh_snapshot", fake_refresh
    )

    await client.put(BASE, json={"enabled": False}, headers={"X-API-Key": admin_api_key})

    assert refreshed["count"] == 1


@pytest.mark.asyncio
async def test_test_connection_reports_reason_without_crashing(client: AsyncClient,
                                                              admin_api_key: str):
    response = await client.post(f"{BASE}/test", headers={"X-API-Key": admin_api_key})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "未启用" in body["message"]


@pytest.mark.asyncio
async def test_test_connection_surfaces_probe_failure(client: AsyncClient, admin_api_key: str,
                                                      monkeypatch):
    async def fake_probe(_overrides=None):
        return (False, "无法连接 Langfuse（DNS 或网络不可达）")

    monkeypatch.setattr("app.services.ai.observability.manager.test_connection", fake_probe)

    response = await client.post(f"{BASE}/test", headers={"X-API-Key": admin_api_key})

    assert response.status_code == 200
    assert response.json() == {"success": False, "message": "无法连接 Langfuse（DNS 或网络不可达）"}


@pytest.mark.asyncio
async def test_test_connection_success_payload(client: AsyncClient, admin_api_key: str,
                                               monkeypatch):
    async def fake_probe(_overrides=None):
        return (True, "连接成功")

    monkeypatch.setattr("app.services.ai.observability.manager.test_connection", fake_probe)

    response = await client.post(f"{BASE}/test", headers={"X-API-Key": admin_api_key})

    assert response.json() == {"success": True, "message": "连接成功"}


@pytest.mark.asyncio
async def test_put_requires_dedicated_element_permission(client: AsyncClient,
                                                        valid_api_key: str):
    """保存必须走独立的 element:system:langfuse_save 权限，普通用户应被拒绝。"""
    response = await client.put(
        BASE,
        json={"enabled": True},
        headers={"X-API-Key": valid_api_key},
    )

    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_test_connection_forwards_draft_form_values(client: AsyncClient,
                                                          admin_api_key: str, monkeypatch):
    """未保存的表单值必须透传给探测，否则用户"必须先保存才能测"。"""
    seen = {}

    async def fake_probe(overrides=None):
        seen["overrides"] = overrides
        return (True, "连接成功")

    monkeypatch.setattr("app.services.ai.observability.manager.test_connection", fake_probe)

    await client.post(
        f"{BASE}/test",
        json={
            "enabled": True,
            "host": "http://draft:3000",
            "public_key": "pk-draft",
            "secret_key": "sk-draft",
            "timeout_seconds": 9,
        },
        headers={"X-API-Key": admin_api_key},
    )

    assert seen["overrides"]["host"] == "http://draft:3000"
    assert seen["overrides"]["public_key"] == "pk-draft"
    assert seen["overrides"]["secret_key"] == "sk-draft"
    assert seen["overrides"]["timeout_seconds"] == 9
    assert seen["overrides"]["enabled"] is True


@pytest.mark.asyncio
async def test_test_connection_without_body_still_works(client: AsyncClient,
                                                        admin_api_key: str, monkeypatch):
    """保持向后兼容：不带 body 时按已保存配置探测。"""
    seen = {}

    async def fake_probe(overrides=None):
        seen["overrides"] = overrides
        return (True, "连接成功")

    monkeypatch.setattr("app.services.ai.observability.manager.test_connection", fake_probe)

    response = await client.post(f"{BASE}/test", headers={"X-API-Key": admin_api_key})

    assert response.status_code == 200
    # 未提交的字段不进 overrides，交由 manager 回落到已保存配置
    assert not (seen["overrides"] or {}).get("host")


@pytest.mark.asyncio
async def test_test_connection_never_persists_draft_values(client: AsyncClient,
                                                           admin_api_key: str, monkeypatch):
    """探测是只读操作：绝不能把草稿值写库。"""
    saved = {"calls": 0}

    async def fake_save(payload, changed_by=None):
        saved["calls"] += 1
        return {}

    async def fake_probe(overrides=None):
        return (True, "连接成功")

    monkeypatch.setattr(config_store, "save_config", fake_save)
    monkeypatch.setattr("app.services.ai.observability.manager.test_connection", fake_probe)

    await client.post(
        f"{BASE}/test",
        json={"host": "http://draft:3000"},
        headers={"X-API-Key": admin_api_key},
    )

    assert saved["calls"] == 0
