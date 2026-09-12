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


@pytest.mark.asyncio
async def test_lazy_sandbox_bash_check_permissions_never_returns_none():
    """验证 LazySandboxBashNativeTool 和外层适配器在沙箱未拉起时 check_permissions 决不返回 None。"""
    from agentscope.permission import PermissionBehavior
    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    lazy_bash = LazySandboxBashNativeTool(
        proxy=MagicMock(),
        local_ws=MagicMock(),
        name="Bash",
    )
    # 1. 原始 lazy bash 工具权限检查
    decision = await lazy_bash.check_permissions({}, None)
    assert decision is not None
    assert hasattr(decision, "behavior")
    assert decision.behavior == PermissionBehavior.ALLOW

    # 2. 外层 AgentScopeNativeApprovalTool 包装后权限检查
    adapter = AgentScopeNativeApprovalTool(
        lazy_bash,
    )
    adapter_decision = await adapter.check_permissions({}, None)
    assert adapter_decision is not None
    assert hasattr(adapter_decision, "behavior")
    assert adapter_decision.behavior == PermissionBehavior.ALLOW


@pytest.mark.asyncio
async def test_bash_lazy_tool_forwards_bash_node_parent_to_ensure_ready(tmp_path):
    """Bash 触发拉起时，把 Bash 卡片节点 id 透传给 ensure_ready 作为 parent_id，并用独立 log_id。"""
    from app.services.ai.runtime.agentscope.workspace import (
        current_bash_tool_parent_id,
    )

    captured = {}

    async def _fake_ensure_ready(**kwargs):
        captured.update(kwargs)
        empty = MagicMock()

        async def _fake_acquire():
            return empty, "k"

        empty._acquire_impl = None
        # 复用现有 mcp 解析路径：模拟真实 WS + Bash MCP
        mock_bash = AsyncMock(return_value="res")
        mock_bash.name = "Bash"
        fake_mcp = MagicMock()
        fake_mcp.name = "sandbox"
        fake_mcp.is_connected = True
        fake_mcp.get_tool.return_value = mock_bash
        empty.is_alive = True
        empty._platform_sandbox_policy = SANDBOX_POLICY_DOCKER
        empty.list_mcps.return_value = [fake_mcp]
        return empty

    fake_proxy = MagicMock()
    fake_proxy.ensure_ready = _fake_ensure_ready

    lazy_bash = LazySandboxBashNativeTool(
        proxy=fake_proxy,
        local_ws=MagicMock(),
        name="Bash",
    )

    bash_id = "toolcall_bash_001"
    reset = current_bash_tool_parent_id.set(bash_id)
    try:
        res = await lazy_bash(command="echo hi")
        assert str(res) == "res"
    finally:
        current_bash_tool_parent_id.reset(reset)

    # 调用方转发：parent_id 用 Bash 卡片 id；log_id 独立（区别于 prep 占位符 workspace:sandbox）
    assert captured.get("parent_id") == bash_id
    assert captured.get("log_id") == f"workspace:sandbox:{bash_id}"


@pytest.mark.asyncio
async def test_bash_lazy_tool_falls_back_to_preparation_without_contextvar():
    """未 seed Bash 节点 id（文件工具 / 预检等）时，ensure_ready 保持 preparation 默认。"""
    captured = {}

    async def _fake_ensure_ready(**kwargs):
        captured.update(kwargs)
        empty = MagicMock()
        mock_bash = AsyncMock(return_value="fallback")
        mock_bash.name = "Bash"
        fake_mcp = MagicMock()
        fake_mcp.name = "sandbox"
        fake_mcp.is_connected = True
        fake_mcp.get_tool.return_value = mock_bash
        empty.is_alive = True
        empty._platform_sandbox_policy = SANDBOX_POLICY_DOCKER
        empty.list_mcps.return_value = [fake_mcp]
        return empty

    fake_proxy = MagicMock()
    fake_proxy.ensure_ready = _fake_ensure_ready

    lazy_bash = LazySandboxBashNativeTool(
        proxy=fake_proxy,
        local_ws=MagicMock(),
        name="Bash",
    )
    res = await lazy_bash(command="echo x")
    assert str(res) == "fallback"

    # parent_id 缺省传给 None，log_id 缺省为 None —— ensure_ready 内部回退 preparation
    assert captured.get("parent_id") is None
    assert captured.get("log_id") is None


@pytest.mark.asyncio
async def test_bash_sandbox_parent_link_middleware_seeds_only_bash():
    """BashSandboxParentLinkMiddleware 只对 Bash 工具 seed 节点 id，非 Bash 保持不写入。"""
    from app.services.ai.runtime.agentscope.middleware import (
        BashSandboxParentLinkMiddleware,
    )
    from app.services.ai.runtime.agentscope.workspace import (
        current_bash_tool_parent_id,
    )

    def _tool(name: str):
        t = MagicMock()
        t.name = name
        return t

    def _make_tool_call(node_id: str, name: str):
        return {"id": node_id, "name": name}

    entered = []

    async def _next_handler(**kwargs):
        entered.append(kwargs)
        return "decision"

    mw = BashSandboxParentLinkMiddleware()
    # 重置 ContextVar 到确定状态
    reset = current_bash_tool_parent_id.set("")
    try:
        # 非 Bash 工具：不写入
        await mw.on_check_permission(
            agent=None,
            input_kwargs={"tool": _tool("Read"), "tool_call": _make_tool_call("foo", "Read")},
            next_handler=_next_handler,
        )
        assert current_bash_tool_parent_id.get() == ""

        # Bash 工具：写入 tool_call.id
        await mw.on_check_permission(
            agent=None,
            input_kwargs={"tool": _tool("Bash"), "tool_call": _make_tool_call("toolcall_bash_002", "Bash")},
            next_handler=_next_handler,
        )
        assert current_bash_tool_parent_id.get() == "toolcall_bash_002"
    finally:
        current_bash_tool_parent_id.reset(reset)

