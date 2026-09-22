import logging

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_sandbox_connection_endpoint_closes_initialized_workspace(monkeypatch):
    from app.api.v1.endpoints.sandbox import (
        SandboxConnectionTestRequest,
        test_sandbox_connection,
    )

    class FakeWorkspace:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    workspace = FakeWorkspace()
    captured = {}

    async def fake_build(policy, config_overrides):
        captured["policy"] = policy
        captured["config_overrides"] = config_overrides
        return workspace

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.build_sandbox_workspace_for_test",
        fake_build,
    )

    response = await test_sandbox_connection(
        policy="e2b",
        body=SandboxConnectionTestRequest(
            sandbox_e2b_api_key="e2b-test-key",
            sandbox_e2b_template="base",
            sandbox_e2b_timeout_seconds="30",
        ),
        user_info={"role": "admin"},
    )

    assert response.data == {"policy": "e2b", "connected": True}
    assert captured["policy"] == "e2b"
    assert captured["config_overrides"]["sandbox_e2b_api_key"] == "e2b-test-key"
    assert workspace.closed is True


@pytest.mark.asyncio
async def test_sandbox_connection_failure_logs_sanitized_reason(monkeypatch, caplog):
    """失败日志必须带上真实原因（否则只剩 error_type 无法排障）且不含凭据。"""
    from fastapi import HTTPException

    from app.api.v1.endpoints.sandbox import (
        SandboxConnectionTestRequest,
        test_sandbox_connection,
    )

    async def fake_build(policy, config_overrides):
        raise RuntimeError(
            "SshWorkspace: cannot reach test@10.0.0.9:3333 over ssh "
            "(auth_type=password): password=SuperSecret123 rejected"
        )

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.build_sandbox_workspace_for_test",
        fake_build,
    )

    with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.sandbox"):
        with pytest.raises(HTTPException) as exc_info:
            await test_sandbox_connection(
                policy="ssh",
                body=SandboxConnectionTestRequest(sandbox_ssh_host="10.0.0.9"),
                user_info={"role": "admin"},
            )

    assert exc_info.value.status_code == 502
    assert "cannot reach test@10.0.0.9:3333" in exc_info.value.detail

    log_text = caplog.text
    assert "cannot reach test@10.0.0.9:3333" in log_text
    assert "SuperSecret123" not in log_text
    assert "password=******" in log_text


@pytest.mark.asyncio
async def test_docker_prebuild_status_returns_manual_download_state(monkeypatch):
    from app.api.v1.endpoints.sandbox import get_docker_prebuild_status

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.docker_workspace_prebuild_status",
        lambda base_image=None: _async_value(
            {
                "prebuilt": False,
                "docker_available": False,
                "action": "manual_download",
                "download_url": "https://downloads.example.com/agentscope-workspace.tar",
                "required_image_tag": "agentscope-workspace:abc123def456",
            },
        ),
    )

    response = await get_docker_prebuild_status(user_info={"role": "admin"})

    assert response.data["action"] == "manual_download"
    assert response.data["docker_available"] is False
    assert response.data["download_url"].startswith("https://")


@pytest.mark.asyncio
async def test_docker_prebuild_stream_returns_sse_events(monkeypatch):
    from app.api.v1.endpoints.sandbox import stream_docker_prebuild

    class FakeRequest:
        async def is_disconnected(self):
            return False

    async def fake_prebuild(*, base_image, force, on_event):
        assert base_image == "python:3.11-slim"
        assert force is False
        await on_event({"type": "phase", "stage": "build", "message": "开始构建"})
        await on_event({"type": "log", "level": "info", "message": "Step 1/2"})
        return {"reused": False, "built": True, "tag": "agentscope-workspace:test123456"}

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.prebuild_docker_workspace_image",
        fake_prebuild,
    )

    response = await stream_docker_prebuild(
        base_image="python:3.11-slim",
        request=FakeRequest(),
        user_info={"role": "admin"},
    )
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunks)

    assert response.media_type == "text/event-stream"
    assert "event: phase" in body
    assert "开始构建" in body
    assert "event: log" in body
    assert "Step 1/2" in body
    assert "event: result" in body
    assert "agentscope-workspace:test123456" in body


@pytest.mark.asyncio
async def test_ensure_docker_workspace_returns_running_metadata(monkeypatch):
    from app.api.v1.endpoints.sandbox import (
        DockerWorkspaceEnsureRequest,
        ensure_docker_workspace_endpoint,
    )

    fake_workspace = type(
        "FakeWorkspace",
        (),
        {
            "_platform_sandbox_policy": "docker",
            "_platform_execution_backend": "docker",
            "_platform_workspace_id": "alice__1",
            "_platform_container_id": "container-1",
            "is_alive": True,
        },
    )()
    captured = {}

    async def fake_ensure(**kwargs):
        captured.update(kwargs)
        return fake_workspace

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_docker_workspace_runtime",
        fake_ensure,
    )

    response = await ensure_docker_workspace_endpoint(
        body=DockerWorkspaceEnsureRequest(conversation_id="c1"),
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
    )

    assert response.data == {
        "status": "running",
        "execution_backend": "docker",
        "workspace_id": "alice__1",
        "container_id": "container-1",
        "started_at": None,
        "uptime_seconds": None,
    }
    assert captured["user_id"] == 1
    assert captured["user_name"] == "alice"
    assert captured["conversation_id"] == "c1"


@pytest.mark.asyncio
async def test_ensure_docker_workspace_rejects_non_docker_policy(monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints.sandbox import (
        DockerWorkspaceEnsureRequest,
        ensure_docker_workspace_endpoint,
    )
    from app.services.ai.runtime.agentscope.workspace import DockerSandboxUnavailableError

    async def fake_ensure(**kwargs):
        raise DockerSandboxUnavailableError(
            "sandbox policy is local",
            reason_code="docker_policy_not_effective",
            user_message="当前不是 Docker 沙箱模式，无需启动用户 Docker 容器。",
        )

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.ensure_docker_workspace_runtime",
        fake_ensure,
    )

    with pytest.raises(HTTPException) as exc_info:
        await ensure_docker_workspace_endpoint(
            body=DockerWorkspaceEnsureRequest(conversation_id="c1"),
            user_info={"user_id": 1, "user_name": "alice", "role": "user"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "reason_code": "docker_policy_not_effective",
        "message": "当前不是 Docker 沙箱模式，无需启动用户 Docker 容器。",
    }


@pytest.mark.asyncio
async def test_docker_workspace_status_returns_existing_container_metadata(monkeypatch):
    from app.api.v1.endpoints.sandbox import get_docker_workspace_status_endpoint

    captured = {}

    async def fake_status(**kwargs):
        captured.update(kwargs)
        return {
            "status": "running",
            "execution_backend": "docker",
            "workspace_id": "alice__1",
            "container_id": "container-1",
        }

    monkeypatch.setattr(
        "app.api.v1.endpoints.sandbox.docker_workspace_status_runtime",
        fake_status,
    )

    response = await get_docker_workspace_status_endpoint(
        conversation_id="c1",
        user_info={"user_id": 1, "user_name": "alice", "role": "user"},
    )

    assert response.data == {
        "status": "running",
        "execution_backend": "docker",
        "workspace_id": "alice__1",
        "container_id": "container-1",
    }
    assert captured["conversation_id"] == "c1"
    assert captured["user_id"] == 1
    assert captured["user_name"] == "alice"


async def _async_value(value):
    return value
