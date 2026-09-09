import pytest
from app.api.v1.endpoints.sandbox import (
    DockerWorkspaceEnsureRequest,
    ensure_k8s_workspace_endpoint,
    get_k8s_workspace_status_endpoint,
    restart_k8s_workspace_endpoint,
    stop_k8s_workspace_endpoint,
)
from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_get_k8s_workspace_status_endpoint(monkeypatch):
    captured = {}

    async def fake_status(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "running": True,
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": "as-ws-alice__1",
            "started_at": "2026-09-09T10:00:00Z",
            "uptime_seconds": 120,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.k8s_workspace_status_runtime",
        fake_status,
    )
    response = await get_k8s_workspace_status_endpoint(
        conversation_id="conv-1",
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"
    assert response.data["pod_name"] == "as-ws-alice__1"
    assert captured["conversation_id"] == "conv-1"
    assert captured["user_id"] == 1


@pytest.mark.asyncio
async def test_ensure_k8s_workspace_endpoint(monkeypatch):
    captured = {}

    async def fake_ensure(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": "as-ws-alice__1",
            "started_at": "2026-09-09T10:00:00Z",
            "uptime_seconds": 0,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_k8s_workspace_runtime",
        fake_ensure,
    )
    response = await ensure_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"
    assert captured["conversation_id"] == "conv-1"


@pytest.mark.asyncio
async def test_stop_k8s_workspace_endpoint(monkeypatch):
    async def fake_stop(**kwargs):
        return {
            "status": "stopped",
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": None,
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.stop_k8s_workspace_runtime",
        fake_stop,
    )
    response = await stop_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "stopped"


@pytest.mark.asyncio
async def test_restart_k8s_workspace_endpoint(monkeypatch):
    async def fake_restart(**kwargs):
        return {
            "status": "running",
            "execution_backend": "k8s",
            "workspace_id": "alice__1",
            "pod_name": "as-ws-alice__1",
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.restart_k8s_workspace_runtime",
        fake_restart,
    )
    response = await restart_k8s_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
        user_info={"id": 1, "username": "alice"},
    )
    assert response.data["status"] == "running"


@pytest.mark.asyncio
async def test_ensure_k8s_workspace_endpoint_policy_not_effective(monkeypatch):
    async def fake_ensure(**kwargs):
        raise K8sSandboxUnavailableError(
            "policy not effective",
            reason_code="k8s_policy_not_effective",
            user_message="当前不是 Kubernetes 沙箱模式，无需操作沙箱 Pod。",
        )

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_k8s_workspace_runtime",
        fake_ensure,
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await ensure_k8s_workspace_endpoint(
            body=DockerWorkspaceEnsureRequest(conversation_id="conv-1"),
            user_info={"id": 1, "username": "alice"},
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["reason_code"] == "k8s_policy_not_effective"
