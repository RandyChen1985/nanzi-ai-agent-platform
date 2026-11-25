from __future__ import annotations

import asyncio
import os
import time
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
    _docker_workspace_verified_at,
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

    # 预先放入缓存。键必须用与 _acquire_docker_workspace 相同的算键函数：
    # 手写键格式（此前少了技能集合令牌）会恒不命中，于是"已存活就直接复用"永远走不到，
    # 一律退化成 lazy 代理——连"建会话即持有引用"的语义都丢了。
    from app.services.ai.runtime.agentscope.workspace import (
        _docker_workspace_cache_key,
        _resolve_sandbox_user_key,
    )

    user_key = _resolve_sandbox_user_key(user_id="101", user_name="tester", user_info={"user_id": 101, "user_name": "tester"})
    wanted_skills = ["/s/only-a"]
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.discover_platform_skill_paths",
        lambda **kwargs: list(wanted_skills),
    )
    docker_key = _docker_workspace_cache_key(str(tmp_path), user_key, wanted_skills)
    _docker_workspace_cache[docker_key] = fake_alive_ws
    # 「刚确认活着」是直接复用的前提：只有存活探测窗口内的条目才会走 eager。
    # 只剩 is_alive=True 的旧条目（容器可能早被空闲回收）一律交惰性代理，否则会在
    # 首 token 之前当场重建一台容器——那正是「工作区准备十几秒」的来源。
    _docker_workspace_verified_at[docker_key] = time.monotonic()

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



@pytest.mark.asyncio
async def test_lazy_k8s_bash_gets_session_cwd_wrapper(tmp_path, monkeypatch):
    """K8s 懒加载 Bash 必须与 Docker 一样套上「会话 cwd」包装。

    历史问题：该包装（以及 ``bind_configured_tools_to_workspace`` 里取宿主根的总开关）
    只判 ``policy == docker``，于是 K8s 沙箱里 Bash 的默认 cwd 停在 ``/workspace``，
    而提示词承诺的是当前会话目录；相对路径写入会落到工作区根。
    """
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value=SANDBOX_POLICY_K8S),
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        AsyncMock(return_value=str(tmp_path)),
    )

    fake_real_ws = MagicMock()
    fake_real_ws.is_alive = True
    fake_real_ws._platform_sandbox_policy = SANDBOX_POLICY_K8S

    mock_bash = AsyncMock(return_value="hello from k8s sandbox")
    mock_bash.name = "Bash"
    fake_mcp_client = MagicMock()
    fake_mcp_client.name = "sandbox"
    fake_mcp_client.is_connected = True
    fake_mcp_client.get_tool.return_value = mock_bash
    fake_real_ws.list_mcps.return_value = [fake_mcp_client]

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace._acquire_k8s_workspace",
        AsyncMock(return_value=(fake_real_ws, "fake_k8s_cache_key")),
    )

    event_queue = asyncio.Queue()
    set_agent_context(
        AgentContext(
            agent_id="test_agent",
            agent_name="Tester",
            user_id=101,
            conversation_id="conv_k8s_lazy",
            event_queue=event_queue,
        )
    )

    pair = await get_local_workspace(
        user_id="101",
        conversation_id="conv_k8s_lazy",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
        lazy_sandbox=True,
    )
    sandbox_ws, local_ws = pair
    assert isinstance(sandbox_ws, LazySandboxWorkspaceProxy)
    # K8s 也必须解析出宿主根，否则包装条件永远不成立（对应 total switch 的放开）
    assert getattr(local_ws, "workspace_user_root", None)

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
    res = await bound[0].callable(command="echo hi")
    assert "hello from k8s sandbox" in str(res)

    from app.services.ai.runtime.agentscope.workspace import (
        _SandboxSessionBashNativeTool,
        _ScriptCredentialBashNativeTool,
    )

    proxy_bash = bound[0].native_tool
    # 最外层是凭据注入包装，剥掉它才看得到 cwd 包装链。
    assert isinstance(proxy_bash, _ScriptCredentialBashNativeTool)
    proxy_bash = proxy_bash._native_tool
    assert isinstance(proxy_bash, LazySandboxBashNativeTool)
    assert isinstance(proxy_bash._real_tool, _SandboxSessionBashNativeTool), (
        "K8s 懒加载 Bash 必须套上会话 cwd 包装，否则默认 cwd 停在 /workspace"
    )
    expected_rel = os.path.relpath(local_ws.workdir, local_ws.workspace_user_root)
    # 未显式指定 cwd 时，默认落到当前会话目录
    assert proxy_bash._real_tool._default_cwd() == expected_rel
    assert proxy_bash._real_tool._map_cwd("") == expected_rel
    # 显式传入沙箱逻辑根 /workspace 时映射为沙箱根相对路径 "."
    assert proxy_bash._real_tool._map_cwd("/workspace") == "."


@pytest.mark.asyncio
async def test_bind_configured_tools_wraps_k8s_sandbox_bash(tmp_path, monkeypatch):
    """K8s（非懒加载）下 Bash 也必须走宿主路径包装。

    覆盖 ``bind_configured_tools_to_workspace`` 里取宿主根的总开关：只判 docker 时，
    K8s 会丢两件事——Bash 默认 cwd 停在 ``/workspace``（而不是当前会话目录），以及绝对
    ``Glob.pattern`` 不被翻译（只有 ``_SandboxLogicalWorkspaceNativeTool`` 会改 pattern，
    ``_WorkspaceFileAccessNativeTool`` 只改 path）。
    """
    from app.services.ai.runtime.agentscope.workspace import (
        _SandboxSessionBashNativeTool,
    )
    from app.services.ai.tools.registry import ToolRegistry

    # 用真实 LocalWorkspace 提供 Read/Write/Glob/Grep，再手动补上只有沙箱策略才会
    # 设置的 workspace_user_root（对应 workspace.py 里放开到 K8s 的那处赋值）
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        AsyncMock(return_value="local"),
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.workspace.resolve_workspace_root",
        AsyncMock(return_value=str(tmp_path)),
    )
    _, local_ws = await get_local_workspace(
        user_id=101,
        conversation_id="conv_k8s_eager",
        user_name="tester",
        user_info={"user_id": 101, "user_name": "tester"},
    )
    user_root = tmp_path / "tester__101"
    local_ws.workspace_user_root = str(user_root)

    fake_k8s_ws = MagicMock()
    fake_k8s_ws.is_alive = True
    fake_k8s_ws._platform_sandbox_policy = SANDBOX_POLICY_K8S
    fake_k8s_ws._platform_sandbox_file_tool_mount_mappings = {}

    mock_bash = AsyncMock(return_value="ok from k8s")
    mock_bash.name = "Bash"
    mock_client = MagicMock()
    mock_client.name = "sandbox"
    mock_client.is_connected = True
    mock_client.get_tool.return_value = mock_bash
    fake_k8s_ws.list_mcps.return_value = [mock_client]

    specs = await ToolRegistry.get_runtime_tools(["exec_command"])
    bound = await bind_configured_tools_to_workspace(
        (fake_k8s_ws, local_ws),
        specs,
        user_info={"user_id": 101, "user_name": "tester"},
    )
    by_name = {spec.name: spec for spec in bound}

    from app.services.ai.runtime.agentscope.workspace import (
        _ScriptCredentialBashNativeTool,
    )

    outer_bash = by_name["Bash"].native_tool
    # 最外层是凭据注入包装；剥掉后必须是会话 cwd 包装。
    assert isinstance(outer_bash, _ScriptCredentialBashNativeTool)
    inner_bash = outer_bash._native_tool
    assert isinstance(inner_bash, _SandboxSessionBashNativeTool), (
        "K8s 非懒加载 Bash 必须套上会话 cwd 包装"
    )
    assert inner_bash._default_cwd() == os.path.relpath(
        local_ws.workdir, str(user_root)
    )


# ---------------------------------------------------------------------------
# Bash 前状态核对：命令内容拦不住，但"这台沙箱还在不在"必须拦
# ---------------------------------------------------------------------------


def _evicted_bash_tool(*, policy: str = SANDBOX_POLICY_DOCKER, registered: bool):
    """构造一个「已解析好真实工具」的懒加载 Bash，沙箱键按需登记。"""
    from app.services.ai.runtime.agentscope import workspace as ws_module

    proxy = LazySandboxWorkspaceProxy(
        policy=policy,
        root="/data",
        user_key="u1",
        skill_paths=["/s/alpha"],
    )
    tool = LazySandboxBashNativeTool(proxy)
    tool._real_tool = AsyncMock(return_value="ran")  # 绕过 ensure_ready，直接测核对点
    if policy == SANDBOX_POLICY_DOCKER:
        key = ws_module._docker_workspace_cache_key("/data", "u1", ["/s/alpha"])
        cache = ws_module._docker_workspace_cache
    else:
        key = ws_module._k8s_workspace_cache_key("/data", "u1", ["/s/alpha"])
        cache = ws_module._k8s_workspace_cache
    proxy._sandbox_cache_key = key
    if registered:
        cache[key] = MagicMock()
        ws_module._docker_workspace_last_used[key] = 0.0
    return tool, key


@pytest.mark.asyncio
async def test_bash_refuses_after_sandbox_evicted():
    """★ 沙箱被逐出（技能权限变更 / 手动停止）后，本次 Bash 必须拒绝而不是打空容器。"""
    from app.services.ai.runtime.agentscope.workspace import DockerSandboxUnavailableError

    tool, key = _evicted_bash_tool(registered=False)
    with pytest.raises(DockerSandboxUnavailableError) as excinfo:
        await tool(command="echo hi")
    assert excinfo.value.reason_code == "docker_sandbox_evicted"
    assert "未执行" in (excinfo.value.user_message or "")
    assert key not in _docker_workspace_cache


@pytest.mark.asyncio
async def test_bash_refuses_after_k8s_sandbox_evicted():
    """K8s 同理，且抛的是 K8s 专用异常（前端提示与降级路径不同）。"""
    from app.services.ai.runtime.agentscope.k8s_workspace import K8sSandboxUnavailableError

    tool, _key = _evicted_bash_tool(policy=SANDBOX_POLICY_K8S, registered=False)
    with pytest.raises(K8sSandboxUnavailableError) as excinfo:
        await tool(command="echo hi")
    assert excinfo.value.reason_code == "k8s_sandbox_evicted"


@pytest.mark.asyncio
async def test_bash_allows_and_touches_registered_sandbox():
    """沙箱仍在注册表里：放行，并刷新 last_used（长时间连续 Bash 不被误回收）。"""
    from app.services.ai.runtime.agentscope.workspace import _docker_workspace_last_used

    tool, key = _evicted_bash_tool(registered=True)
    _docker_workspace_last_used[key] = 0.0
    try:
        assert await tool(command="echo hi") == "ran"
        assert _docker_workspace_last_used[key] > 0.0, "每次 Bash 都应 touch，避免长任务被 reaper 回收"
    finally:
        _docker_workspace_cache.pop(key, None)
        _docker_workspace_last_used.pop(key, None)


@pytest.mark.asyncio
async def test_bash_tolerates_unknown_cache_key_shape():
    """测试替身/异常形态的键不参与核对（无从判断是否被逐出时放行，避免误杀）。"""
    proxy = LazySandboxWorkspaceProxy(
        policy=SANDBOX_POLICY_DOCKER, root="/data", user_key="u1", skill_paths=["/s/alpha"]
    )
    tool = LazySandboxBashNativeTool(proxy)
    tool._real_tool = AsyncMock(return_value="ran")
    proxy._sandbox_cache_key = "fake_cache_key"
    assert await tool(command="echo hi") == "ran"


@pytest.mark.asyncio
async def test_ensure_docker_workspace_forwards_skills_scope(monkeypatch, tmp_path):
    """★ 预热（ensure）必须把会话的技能范围传给 get_local_workspace。

    沙箱身份 = 用户 + 技能集合令牌，预热漏了这两项就会按全量技能另建一台容器：用户
    看到「沙箱已经 warm 起来了」却仍然在「工作区准备 → 沙箱获取」等十几秒，而预热那台
    空占到空闲回收。
    """
    from app.services.ai.runtime.agentscope import workspace as _ws

    seen: dict = {}

    async def fake_get_local_workspace(**kwargs):
        seen.update(kwargs)

        class _Bound:
            _platform_sandbox_policy = "docker"

        return (_Bound(), object())

    monkeypatch.setattr(_ws, "get_local_workspace", fake_get_local_workspace)
    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get", AsyncMock(return_value="docker")
    )
    monkeypatch.setattr(
        "app.services.config_service.resolve_effective_sandbox_policy",
        lambda value, default: "docker",
    )

    await _ws.ensure_docker_workspace(
        user_id=1,
        user_name="alice",
        conversation_id="conv-123",
        skills_custom=True,
        allowed_global_skills=["skill-alpha"],
    )

    assert seen["skills_custom"] is True
    assert seen["allowed_global_skills"] == ["skill-alpha"]
    assert seen["lazy_sandbox"] is False, "预热就是要当场把容器拉起来"
