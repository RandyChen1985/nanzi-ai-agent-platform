from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.context import AgentContext, set_agent_context
from app.services.ai.runtime.agentscope.workspace import (
    LazySandboxBashNativeTool,
    LazySandboxWorkspaceProxy,
    SANDBOX_POLICY_DOCKER,
    SANDBOX_POLICY_K8S,
    bind_configured_tools_to_workspace,
    clear_workspace_cache,
    ensure_docker_workspace,
    ensure_k8s_workspace,
    get_local_workspace,
    _docker_workspace_cache,
    _k8s_workspace_cache,
)
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def _cleanup_workspace():
    clear_workspace_cache()
    _docker_workspace_cache.clear()
    _k8s_workspace_cache.clear()
    yield
    clear_workspace_cache()
    _docker_workspace_cache.clear()
    _k8s_workspace_cache.clear()


@pytest.mark.asyncio
async def test_lazy_sandbox_proxy_not_started_when_no_bash(tmp_path, monkeypatch):
    """当 lazy_sandbox=True 且没有调用 Bash 时，沙箱绝不启动。"""
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=SANDBOX_POLICY_DOCKER),
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    acquire_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._acquire_docker_workspace",
        acquire_mock,
    )

    pair = await get_local_workspace(
        user_id="101",
        conversation_id="conv_lazy_test",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
        lazy_sandbox=True,
    )
    assert pair is not None
    sandbox_ws, local_ws = pair
    assert isinstance(sandbox_ws, LazySandboxWorkspaceProxy)
    assert not sandbox_ws.is_alive
    acquire_mock.assert_not_called()

    # 绑定只读工具（无 Bash）
    file_spec = RuntimeToolSpec(
        name="Read",
        description="Read file",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        source_type="system",
        callable=AsyncMock(return_value="file content"),
    )
    bound = await bind_configured_tools_to_workspace(
        (sandbox_ws, local_ws),
        [file_spec],
        user_info={"user_id": 101, "user_name": "tester"},
    )
    assert len(bound) == 1
    # 沙箱仍未被拉起
    acquire_mock.assert_not_called()
    assert not sandbox_ws.is_alive


@pytest.mark.asyncio
async def test_lazy_sandbox_bash_call_triggers_startup_and_sse(tmp_path, monkeypatch):
    """当真正调用 Bash 工具时按需拉起沙箱，并通过 event_queue 推送 pending/success 日志。"""
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=SANDBOX_POLICY_DOCKER),
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    # 构造模拟的真实沙箱
    fake_real_ws = MagicMock()
    fake_real_ws.is_alive = True
    fake_real_ws._platform_sandbox_policy = SANDBOX_POLICY_DOCKER

    # 模拟真实 Bash 工具
    mock_bash = AsyncMock(return_value="hello from sandbox")
    mock_bash.name = "Bash"

    fake_mcp_client = MagicMock()
    fake_mcp_client.name = "sandbox"
    fake_mcp_client.is_connected = True
    fake_mcp_client.get_tool.return_value = mock_bash
    fake_real_ws.list_mcps.return_value = [fake_mcp_client]

    acquire_mock = AsyncMock(return_value=(fake_real_ws, "fake_cache_key"))
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._acquire_docker_workspace",
        acquire_mock,
    )

    # 准备 AgentContext 与 event_queue
    event_queue = asyncio.Queue()
    ctx = AgentContext(
        agent_id="test_agent",
        agent_name="Tester",
        user_id=101,
        conversation_id="conv_lazy_test",
        event_queue=event_queue,
    )
    set_agent_context(ctx)

    pair = await get_local_workspace(
        user_id="101",
        conversation_id="conv_lazy_test",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
        lazy_sandbox=True,
    )
    sandbox_ws, local_ws = pair
    assert isinstance(sandbox_ws, LazySandboxWorkspaceProxy)

    bash_spec = RuntimeToolSpec(
        name="Bash",
        description="Run bash command",
        parameters_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        source_type="system",
        callable=AsyncMock(),
    )
    bound = await bind_configured_tools_to_workspace(
        (sandbox_ws, local_ws),
        [bash_spec],
        user_info={"user_id": 101, "user_name": "tester"},
    )
    assert len(bound) == 1
    bound_bash_spec = bound[0]
    # 此时依然未拉起沙箱
    acquire_mock.assert_not_called()

    # 模拟触发调用 Bash 工具
    res = await bound_bash_spec.callable(command="echo 'hello'")
    assert "hello from sandbox" in str(res)

    # 验证沙箱被成功拉起
    acquire_mock.assert_called_once()
    assert sandbox_ws.is_alive

    # 验证 SSE 队列中收到了 pending 和 success 日志
    events = []
    while not event_queue.empty():
        events.append(await event_queue.get())

    assert len(events) >= 2
    assert events[0]["id"] == "workspace:sandbox"
    assert events[0]["status"] == "pending"
    assert events[1]["id"] == "workspace:sandbox"
    assert events[1]["status"] == "success"


@pytest.mark.asyncio
async def test_lazy_sandbox_reuses_already_alive_sandbox(tmp_path, monkeypatch):
    """当沙箱在缓存中已存活时，直接复用不进入 lazy 状态。"""
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=SANDBOX_POLICY_DOCKER),
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    fake_alive_ws = MagicMock()
    fake_alive_ws.is_alive = True
    fake_alive_ws._platform_sandbox_policy = SANDBOX_POLICY_DOCKER

    acquire_mock = AsyncMock(return_value=(fake_alive_ws, "alive_key"))
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._acquire_docker_workspace",
        acquire_mock,
    )

    # 预先放入缓存
    from app.services.ai.runtime.agentscope.workspace import _resolve_sandbox_user_key
    user_key = _resolve_sandbox_user_key(user_id="101", user_name="tester", user_info={"user_id": 101, "user_name": "tester"})
    docker_key = f"{os.path.abspath(str(tmp_path))}::{user_key}::{SANDBOX_POLICY_DOCKER}"
    _docker_workspace_cache[docker_key] = fake_alive_ws

    pair = await get_local_workspace(
        user_id="101",
        conversation_id="conv_already_alive",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
        lazy_sandbox=True,
    )
    sandbox_ws, _local_ws = pair
    # 直接返回已有沙箱对象，而不是 Proxy
    assert not isinstance(sandbox_ws, LazySandboxWorkspaceProxy)
    assert sandbox_ws == fake_alive_ws


@pytest.mark.asyncio
async def test_ensure_endpoints_always_eager(tmp_path, monkeypatch):
    """ensure 端点始终立即拉起沙箱，不返回未就绪 Proxy。"""
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=SANDBOX_POLICY_DOCKER),
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    fake_real_ws = MagicMock()
    fake_real_ws.is_alive = True
    fake_real_ws._platform_sandbox_policy = SANDBOX_POLICY_DOCKER

    acquire_mock = AsyncMock(return_value=(fake_real_ws, "fake_cache_key"))
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._acquire_docker_workspace",
        acquire_mock,
    )

    sandbox_ws = await ensure_docker_workspace(
        user_id="101",
        conversation_id="conv_ensure_test",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
    )
    assert not isinstance(sandbox_ws, LazySandboxWorkspaceProxy)
    assert sandbox_ws == fake_real_ws
    acquire_mock.assert_called_once()
