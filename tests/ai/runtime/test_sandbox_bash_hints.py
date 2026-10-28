"""沙箱内置 Bash 的「环境踩坑」纠偏提示。

背景：沙箱里默认 ``python`` 是网关 venv（``/root/.agentscope/.venv``），裸 ``pip``
却是系统 Python——``pip install`` 装出来的包默认 ``python`` 根本 import 不到。
提示词已经写了正确姿势，但模型仍可能先手搓 ``pip``；工具返回值必须能把它拽回来，
否则它只会换个包名重试。这里锁住三条提示的触发条件。
"""

from __future__ import annotations

import json

import pytest

from app.services.ai.runtime.agentscope import workspace_container_mcp as mcp_module

pytestmark = pytest.mark.no_infrastructure


def _inline_namespace() -> dict:
    """执行内联网关脚本（去掉 ``mcp.run`` 阻塞）并返回其命名空间。"""
    source = mcp_module._INLINE_SERVER
    compile(source, "<sandbox-inline-server>", "exec")
    code = source.replace('mcp.run(transport="stdio")', "")

    class _FakeMCP:
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401 - 测试替身
            pass

        def tool(self):
            def decorator(fn):
                return fn

            return decorator

        def run(self, *args, **kwargs) -> None:
            pass

    namespace: dict = {"__name__": "sandbox_inline_server", "FastMCP": _FakeMCP}
    exec(compile(code, "<sandbox-inline-server>", "exec"), namespace)  # noqa: S102
    return namespace


def test_bare_pip_install_gets_uv_target_hint():
    hints = _inline_namespace()["_dependency_hints"]

    for command in ("pip install pypdf", "pip3 install pypdf", "python -m pip install pypdf"):
        assert hints(command, "", "", 0), command
    # 正确姿势不该被唠叨
    assert hints("uv pip install --target /workspace/.pylibs pypdf", "", "", 0) == []
    assert hints("uv pip install --prefix /workspace/.local pypdf", "", "", 0) == []


def test_uv_install_into_venv_warns_it_is_ephemeral():
    """`uv pip install` 不带 --target 会装进容器 venv：当场可用，回收即丢。"""
    hints = _inline_namespace()["_dependency_hints"]

    result = hints("uv pip install pypdf", "", "", 0)

    assert len(result) == 1
    assert "容器 venv" in result[0]
    assert "--target /workspace/.pylibs" in result[0]


def test_missing_module_gets_persistence_hint():
    hints = _inline_namespace()["_dependency_hints"]

    result = hints("python x.py", "", "ModuleNotFoundError: No module named 'pypdf'", 1)

    assert len(result) == 1
    assert "uv pip install --target /workspace/.pylibs" in result[0]
    assert "只有 /workspace 下的内容会保留" in result[0]


def test_missing_command_gets_ephemeral_hint():
    hints = _inline_namespace()["_dependency_hints"]

    assert hints("jq .", "", "bash: jq: command not found", 127)


def test_bash_payload_includes_hints_key_only_when_needed(tmp_path, monkeypatch):
    # 内联网关脚本用 SANDBOX_WORKDIR 作为工作根；本地跑测试时指向临时目录。
    monkeypatch.setenv("SANDBOX_WORKDIR", str(tmp_path))
    namespace = _inline_namespace()

    clean = json.loads(namespace["bash"]("true"))
    assert "hints" not in clean

    dirty = json.loads(namespace["bash"]("echo 'No module named x' >&2; exit 1"))
    assert dirty["hints"]
