"""脚本工具接入会话工作区：绑定、执行位置、失败不降级。

这是整条链路里最容易出事的接缝：如果绑定环节悄悄退回宿主机，用户看到的仍是
「成功」，但执行位置已经不是沙箱了。所以这里既测正常执行，也测「不该跑的时候
必须报错」。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
from app.services.ai.runtime.agentscope.workspace import _bind_script_tool_spec
from app.services.ai.tools.script_tool_builtins import BUILTIN_SCRIPT_TOOLS, get_builtin_script_tool
from app.services.ai.tools.script_tool_factory import config_from_builtin


def _spec_for(builtin_name: str) -> RuntimeToolSpec:
    from app.services.ai.tools.script_tool_runtime import ScriptToolConfig

    builtin = get_builtin_script_tool(builtin_name)
    assert builtin is not None
    config = config_from_builtin(builtin)
    return RuntimeToolSpec(
        name=config.name,
        description=config.description or config.name,
        parameters_schema=dict(config.parameter_schema or {}),
        callable=None,
        source_type="script_tool",
        metadata={"script_tool_config": config},
    )


def _local_ws(root: Path, session: Path) -> SimpleNamespace:
    return SimpleNamespace(workdir=str(session), workspace_user_root=str(root))


async def test_bound_tool_executes_in_session_workdir(tmp_path):
    root = tmp_path / "users" / "u1"
    session = root / "sessions" / "s1"
    session.mkdir(parents=True)
    (session / "note.txt").write_text("nanzi", encoding="utf-8")

    spec = _spec_for("file_digest")
    bound = _bind_script_tool_spec(spec, sandbox_ws=None, local_ws=_local_ws(root, session), user_info={})
    assert bound.callable is not None

    output = await bound.callable(target="note.txt", algorithm="sha256")
    payload = json.loads(output)
    assert payload["source"] == "file"
    assert payload["size"] == 5


async def test_spec_without_config_is_passed_through():
    """不是脚本工具的 spec（或没带配置）必须原样返回，不能吞掉。"""
    spec = RuntimeToolSpec(
        name="plain",
        description="d",
        parameters_schema={},
        callable=None,
        source_type="script_tool",
    )
    assert _bind_script_tool_spec(spec, sandbox_ws=None, local_ws=SimpleNamespace(), user_info={}) is spec


async def test_missing_workdir_raises_instead_of_falling_back(tmp_path):
    spec = _spec_for("file_digest")
    with pytest.raises(PermissionError):
        _bind_script_tool_spec(
            spec,
            sandbox_ws=None,
            local_ws=SimpleNamespace(workdir=None),
            user_info={},
        )


async def test_script_failure_surfaces_as_tool_output_not_exception(tmp_path):
    """脚本自己 exit 2 属于「业务失败」：返回给模型的是可读文本，不是异常。"""
    root = tmp_path / "u"
    session = root / "s"
    session.mkdir(parents=True)

    spec = _spec_for("file_to_base64")
    bound = _bind_script_tool_spec(spec, sandbox_ws=None, local_ws=_local_ws(root, session), user_info={})
    output = await bound.callable(path="not-exists.txt")
    assert "脚本执行失败" in output
    assert "退出码" in output


async def test_all_builtins_are_bindable():
    """内置工具必须都能构造出可执行 spec（防止某个定义缺字段后静默失效）。"""
    for builtin in BUILTIN_SCRIPT_TOOLS:
        spec = _spec_for(builtin.name)
        assert spec.metadata["script_tool_config"].code.strip()
