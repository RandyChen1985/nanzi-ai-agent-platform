"""管理员沙箱实例运维 HTTP 端点单元测试与权限契约。"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.sandbox import (
    AdminSandboxBatchRequest,
    AdminSandboxCleanupStaleRequest,
    cleanup_stale_admin_sandbox_instances_endpoint,
    get_admin_sandbox_instance_detail_endpoint,
    kill_admin_sandbox_instances_endpoint,
    list_admin_sandbox_instances_endpoint,
    restart_admin_sandbox_instances_endpoint,
)
from app.services.ai.runtime.agentscope.sandbox_admin import SandboxAdminTargetError

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]


def test_admin_sandbox_endpoints_require_config_save_permission():
    source = (ROOT / "app/api/v1/endpoints/sandbox.py").read_text(encoding="utf-8")
    for handler in (
        "list_admin_sandbox_instances_endpoint",
        "get_admin_sandbox_instance_detail_endpoint",
        "kill_admin_sandbox_instances_endpoint",
        "restart_admin_sandbox_instances_endpoint",
        "cleanup_stale_admin_sandbox_instances_endpoint",
    ):
        start = source.index(f"async def {handler}(")
        sig_end = source.index("):", start)
        assert "Depends(SANDBOX_MANAGE_PERMISSION)" in source[start:sig_end], (
            f"{handler} 必须复用 SANDBOX_MANAGE_PERMISSION（element:system:config_save）"
        )


@pytest.mark.asyncio
async def test_list_and_detail_endpoints_forward_and_map_errors(monkeypatch):
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.sandbox_admin.list_admin_sandboxes",
        AsyncMock(return_value={"backend": "docker", "available": True, "items": [], "summary": {"all": 0}}),
    )
    resp = await list_admin_sandbox_instances_endpoint(
        backend="docker", include_stats=False, user_info={"id": 1, "role": "admin"}
    )
    assert resp.data["backend"] == "docker"

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.sandbox_admin.get_admin_sandbox_detail",
        AsyncMock(side_effect=SandboxAdminTargetError("拒绝操作非沙箱容器", status_code=403)),
    )
    with pytest.raises(HTTPException) as exc_info:
        await get_admin_sandbox_instance_detail_endpoint(
            target_id="nanzi-ai-agent",
            backend="docker",
            tail=100,
            user_info={"id": 1, "role": "admin"},
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_batch_kill_restart_and_cleanup_endpoints(monkeypatch):
    with pytest.raises(HTTPException) as empty_kill:
        await kill_admin_sandbox_instances_endpoint(
            body=AdminSandboxBatchRequest(backend="docker", target_ids=[]),
            user_info={"id": 1, "role": "admin"},
        )
    assert empty_kill.value.status_code == 400

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.sandbox_admin.kill_admin_sandboxes",
        AsyncMock(return_value={"backend": "docker", "succeeded": 2, "failed": 0, "results": []}),
    )
    kill_resp = await kill_admin_sandbox_instances_endpoint(
        body=AdminSandboxBatchRequest(backend="docker", target_ids=["c1", "c2"]),
        user_info={"id": 1, "role": "admin"},
    )
    assert kill_resp.data["succeeded"] == 2

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.sandbox_admin.restart_admin_sandboxes",
        AsyncMock(return_value={"backend": "k8s", "succeeded": 1, "failed": 0, "results": []}),
    )
    restart_resp = await restart_admin_sandbox_instances_endpoint(
        body=AdminSandboxBatchRequest(backend="k8s", target_ids=["pod-1"]),
        user_info={"id": 1, "role": "admin"},
    )
    assert restart_resp.data["succeeded"] == 1

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.sandbox_admin.cleanup_stale_admin_sandboxes",
        AsyncMock(return_value={"backend": "docker", "succeeded": 5, "failed": 0, "results": []}),
    )
    clean_resp = await cleanup_stale_admin_sandbox_instances_endpoint(
        body=AdminSandboxCleanupStaleRequest(backend="docker", include_running_orphans=True),
        user_info={"id": 1, "role": "admin"},
    )
    assert clean_resp.data["succeeded"] == 5
