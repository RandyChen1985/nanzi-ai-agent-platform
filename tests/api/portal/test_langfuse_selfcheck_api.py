"""自检接口契约测试。

与 ``test_langfuse_config_api.py`` 相同的隔离策略：**绝不构造真实 Langfuse client**
（构造它会注册全局 TracerProvider，污染同进程后续用例），自检与反查全部打桩。
"""

import pytest
from httpx import AsyncClient

from app.services.ai.observability import (
    config_store,
    export_health,
    manager,
    selfcheck,
    settings,
)

SELFCHECK_URL = "/api/portal/system/langfuse/selfcheck"
CONFIG_URL = "/api/portal/system/langfuse"


@pytest.fixture(autouse=True)
def _hermetic_observability(monkeypatch):
    settings.reset_for_tests()
    manager.reset_for_tests()
    export_health.reset_for_tests()
    monkeypatch.setattr(manager, "_build_client", lambda _snapshot: object())
    yield
    settings.reset_for_tests()
    manager.reset_for_tests()
    export_health.reset_for_tests()


@pytest.mark.asyncio
async def test_selfcheck_requires_api_key(client: AsyncClient):
    response = await client.post(SELFCHECK_URL)
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_selfcheck_requires_dedicated_element_permission(
    client: AsyncClient, valid_api_key: str
):
    """自检会向外部系统发数据，必须有 element:system:langfuse_save。"""
    response = await client.post(SELFCHECK_URL, headers={"X-API-Key": valid_api_key})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_selfcheck_reports_disabled(client: AsyncClient, admin_api_key: str, monkeypatch):
    async def fake_run():
        return selfcheck.SelfCheckResult(False, None, False, "追踪未启用，请先保存并启用链路追踪")

    monkeypatch.setattr(selfcheck, "run_selfcheck", fake_run)
    response = await client.post(SELFCHECK_URL, headers={"X-API-Key": admin_api_key})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["trace_id"] is None
    assert "未启用" in body["message"]


@pytest.mark.asyncio
async def test_selfcheck_returns_trace_id(client: AsyncClient, admin_api_key: str, monkeypatch):
    async def fake_run():
        return selfcheck.SelfCheckResult(True, "a" * 32, True, "导出成功")

    monkeypatch.setattr(selfcheck, "run_selfcheck", fake_run)
    body = (await client.post(SELFCHECK_URL, headers={"X-API-Key": admin_api_key})).json()
    assert body["ok"] is True
    assert body["export_confirmed"] is True
    assert body["trace_id"] == "a" * 32


@pytest.mark.asyncio
async def test_selfcheck_survives_internal_error(
    client: AsyncClient, admin_api_key: str, monkeypatch
):
    """自检内部异常也必须返回结构化结果，而不是 500。"""

    async def boom():
        raise RuntimeError("意外错误")

    monkeypatch.setattr(selfcheck, "run_selfcheck", boom)
    response = await client.post(SELFCHECK_URL, headers={"X-API-Key": admin_api_key})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "意外错误" in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["zz", "a" * 31, "a" * 33, "G" * 32])
async def test_reverse_lookup_rejects_malformed_trace_id(
    client: AsyncClient, admin_api_key: str, bad: str
):
    response = await client.get(f"{SELFCHECK_URL}/{bad}", headers={"X-API-Key": admin_api_key})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reverse_lookup_found(client: AsyncClient, admin_api_key: str, monkeypatch):
    async def fake_query(trace_id):
        return (True, "已到达 Langfuse，可以打开控制台查看")

    monkeypatch.setattr(manager, "query_trace_exists", fake_query)
    body = (
        await client.get(f"{SELFCHECK_URL}/{'a' * 32}", headers={"X-API-Key": admin_api_key})
    ).json()
    assert body["found"] is True


@pytest.mark.asyncio
async def test_reverse_lookup_reports_pending(
    client: AsyncClient, admin_api_key: str, monkeypatch
):
    async def fake_query(trace_id):
        return (False, "尚未查到，Langfuse 可能仍在处理（摄取有延迟）")

    monkeypatch.setattr(manager, "query_trace_exists", fake_query)
    body = (
        await client.get(f"{SELFCHECK_URL}/{'b' * 32}", headers={"X-API-Key": admin_api_key})
    ).json()
    assert body["found"] is False
    assert "尚未查到" in body["message"]


@pytest.mark.asyncio
async def test_get_config_includes_export_health(
    client: AsyncClient, admin_api_key: str, monkeypatch
):
    async def fake_load():
        return config_store.normalize_payload({})

    monkeypatch.setattr(config_store, "load_config", fake_load)
    export_health.record_failure(RuntimeError("boom"))
    export_health.record_success()

    health = (await client.get(CONFIG_URL, headers={"X-API-Key": admin_api_key})).json()[
        "export_health"
    ]
    assert health["success_count"] == 1
    assert health["failure_count"] == 1
    assert health["consecutive_failures"] == 0  # 成功之后连续失败清零
    assert health["last_error"], "最近一次失败原因应保留为历史记录"
    assert health["last_success_at"] is not None
