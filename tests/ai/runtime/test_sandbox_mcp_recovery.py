# -*- coding: utf-8 -*-
"""内置 sandbox MCP 自愈：旧 ``.mcp`` 固化旧脚本 / 两侧版本失配时自动重建。

背景：MCP 声明被 AgentScope 持久化在 ``<workdir>/.mcp``（含内联脚本全文），且
优先级高于构造参数 ``default_mcps``。旧版本写下的 ``from mcp.server.fastmcp
import FastMCP`` 在 mcp 2.x 镜像里直接抛 ``ModuleNotFoundError``，gateway 注册
返回 500，平台拿不到 ``bash``，用户只看到一句「Bash 工具不可用」。

这里守住三条不变量：
1. 拿不到 bash 时自动 remove+add 一次，成功后照常返回工具；
2. 重建失败时 ``.mcp`` 与内存声明逐字节/逐条回滚，用户自建 MCP 一条不丢；
3. 并发 Bash 只重建一次，且只按名字 ``sandbox`` 操作。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.ai.runtime.agentscope import workspace as workspace_module
from app.services.ai.runtime.agentscope.workspace import (
    DockerSandboxUnavailableError,
    LazySandboxBashNativeTool,
    _resolve_sandbox_bash_tool,
)


class _FakeMCPClient:
    """最小可用的 MCPClient 替身：``get_tool("bash")`` 直接给出工具。"""

    def __init__(self, name: str = "sandbox", bash_tool: Any = "bash-tool") -> None:
        self.name = name
        self.is_connected = True
        self._bash_tool = bash_tool

    async def connect(self) -> None:  # pragma: no cover - 已标记连接，不会调用
        raise AssertionError("connected client must not reconnect")

    def get_tool(self, name: str) -> Any:
        return self._bash_tool if name == "bash" else None


class _FakeBackend:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files: dict[str, bytes] = dict(files or {})

    async def file_exists(self, path: str) -> bool:
        return path in self.files

    async def read_file(self, path: str) -> bytes:
        return self.files[path]

    async def write_file(self, path: str, data: bytes) -> None:
        self.files[path] = data

    async def delete_path(self, path: str) -> None:
        self.files.pop(path, None)


class _FakeSandboxWorkspace:
    """模拟 AgentScope 的 gateway workspace（只保留自愈用到的面）。"""

    def __init__(
        self,
        *,
        policy: str = workspace_module.SANDBOX_POLICY_DOCKER,
        empty_list_results: int = 1,
        bash_tool: Any = "bash-tool",
        mcp_bytes: bytes | None = b'{"version": 2, "mcps": {}}',
        fail_add: bool = False,
        fail_remove: bool = False,
        user_mcp: Any | None = None,
    ) -> None:
        self._platform_sandbox_policy = policy
        self._mcp_file = "/workspace/.mcp"
        self._mcp_specs = {
            ("", ""): [_FakeMCPClient(name="sandbox"), user_mcp or _FakeMCPClient(name="other")]
        }
        self._mcp_instances = {("", ""): {"sandbox": object()}}
        self._empty_list_results = empty_list_results
        self._bash_tool = bash_tool
        self._fail_add = fail_add
        self._fail_remove = fail_remove
        self.remove_calls: list[str] = []
        self.add_calls: list[str] = []
        self.list_calls = 0
        self._backend = _FakeBackend({} if mcp_bytes is None else {"/workspace/.mcp": mcp_bytes})

    # ── gateway 面 ────────────────────────────────────────────────
    def list_mcps(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.list_calls += 1
        if self.list_calls <= self._empty_list_results:
            return []
        return [_FakeMCPClient(bash_tool=self._bash_tool)]

    async def remove_mcp(self, name: str, **kwargs: Any) -> None:
        self.remove_calls.append(name)
        if self._fail_remove:
            raise RuntimeError("gateway DELETE failed")
        self._mcp_specs[("", "")] = [
            spec for spec in self._mcp_specs.get(("", ""), []) if getattr(spec, "name", None) != name
        ]

    async def add_mcp(self, client: Any, **kwargs: Any) -> Any:
        self.add_calls.append(getattr(client, "name", ""))
        if self._fail_add:
            raise RuntimeError("gateway POST /mcps 500")
        self._mcp_specs[("", "")] = [*self._mcp_specs.get(("", ""), []), client]
        return client

    def get_backend(self) -> _FakeBackend:
        return self._backend


@pytest.mark.asyncio
async def test_rebuilds_stale_sandbox_mcp_and_returns_bash_tool():
    """★ 旧声明导致取不到 bash 时，重建一次后照常返回工具。"""
    ws = _FakeSandboxWorkspace(empty_list_results=2)

    tool, failure = await _resolve_sandbox_bash_tool(ws)

    assert tool == "bash-tool"
    assert failure == ""
    assert ws.remove_calls == ["sandbox"]
    assert ws.add_calls == ["sandbox"]
    assert ws.list_calls == 3, "直接取一次、等锁后复查一次、重建后再取一次"
    assert len(ws._mcp_specs[("", "")]) == 2, "用户自建 MCP 必须保留"


@pytest.mark.asyncio
async def test_rebuild_failure_rolls_back_file_and_declarations():
    """★ add_mcp 失败：``.mcp`` 与内存声明原样回滚，绝不留下空列表。"""
    original = b'{"version": 2, "mcps": {"": {"": [{"name": "other"}]}}}'
    user_mcp = _FakeMCPClient(name="other")
    ws = _FakeSandboxWorkspace(
        empty_list_results=3, mcp_bytes=original, fail_add=True, user_mcp=user_mcp
    )
    before_specs = list(ws._mcp_specs[("", "")])

    tool, failure = await _resolve_sandbox_bash_tool(ws)

    assert tool is None
    assert "RuntimeError" in failure and "500" in failure
    assert ws._backend.files["/workspace/.mcp"] == original, ".mcp 必须逐字节恢复"
    assert [id(s) for s in ws._mcp_specs[("", "")]] == [id(s) for s in before_specs]
    assert ws._mcp_instances.get(("", "")) is None, "失败留下的半状态客户端必须清掉"


@pytest.mark.asyncio
async def test_concurrent_bash_calls_rebuild_only_once():
    """★ 并发 Bash 只重建一次（per-workspace 锁 + 标记），第二个直接复用结果。"""
    ws = _FakeSandboxWorkspace(empty_list_results=2)

    results = await asyncio.gather(
        _resolve_sandbox_bash_tool(ws),
        _resolve_sandbox_bash_tool(ws),
    )

    assert [tool for tool, _ in results] == ["bash-tool", "bash-tool"]
    assert ws.remove_calls == ["sandbox"]
    assert ws.add_calls == ["sandbox"]


@pytest.mark.asyncio
async def test_k8s_sandbox_uses_gateway_venv_interpreter(monkeypatch):
    """K8s 沙箱重建时必须用网关 venv 解释器，否则 mcp 子进程 import 失败。"""
    from app.services.ai.runtime.agentscope import workspace_container_mcp as mcp_module

    captured: dict[str, Any] = {}
    real_builder = mcp_module.build_container_tool_mcp

    def fake_builder(*args: Any, **kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(mcp_module, "build_container_tool_mcp", fake_builder)
    ws = _FakeSandboxWorkspace(
        policy=workspace_module.SANDBOX_POLICY_K8S, empty_list_results=2
    )

    tool, failure = await _resolve_sandbox_bash_tool(ws)

    assert tool == "bash-tool" and failure == ""
    assert captured["kwargs"]["interpreter"] == mcp_module.K8S_GATEWAY_VENV_PYTHON


@pytest.mark.asyncio
async def test_failure_surfaces_real_cause_and_fingerprint_stays_stable():
    """失败原因要带回上层（不再只剩一句 Bash MCP 不可用），且指纹可重复计算。"""
    from app.services.ai.runtime.agentscope.workspace_container_mcp import (
        sandbox_mcp_fingerprint,
    )

    ws = _FakeSandboxWorkspace(empty_list_results=3, fail_remove=True)

    tool, failure = await _resolve_sandbox_bash_tool(ws)

    assert tool is None
    assert "gateway DELETE failed" in failure
    assert sandbox_mcp_fingerprint() == sandbox_mcp_fingerprint()
    assert len(sandbox_mcp_fingerprint()) == 16


@pytest.mark.asyncio
async def test_lazy_bash_reports_mcp_unavailable_reason_code():
    """★ 端到端：自愈失败后抛专用 reason_code，且提示已尝试自动重建。"""
    from unittest.mock import AsyncMock, MagicMock

    proxy = MagicMock()
    proxy.ensure_ready = AsyncMock(
        return_value=_FakeSandboxWorkspace(empty_list_results=3, fail_add=True)
    )
    proxy.policy = workspace_module.SANDBOX_POLICY_DOCKER
    proxy._sandbox_cache_key = None
    tool = LazySandboxBashNativeTool(proxy)

    with pytest.raises(DockerSandboxUnavailableError) as excinfo:
        await tool._resolve_real_tool()

    assert excinfo.value.reason_code == "docker_sandbox_mcp_unavailable"
    assert "已尝试自动重建" in excinfo.value.user_message
    assert "RuntimeError" in str(excinfo.value)
