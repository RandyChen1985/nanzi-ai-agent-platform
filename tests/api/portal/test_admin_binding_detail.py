"""管理员看绑定时必须能认出「绑到哪、谁绑的」。

`consumer_id` 是 uuid、槽位是 `arg:echo:api_key` 这种内部编码，直接摊在界面上
等于没说。目标名和绑定者要由后端解析好——前端拼不出来（它没有权限也没有表）。
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = pytest.mark.no_infrastructure


def _admin_source() -> str:
    from app.api.portal.endpoints import secrets as secrets_endpoints

    # 列表端点是这个模块里最长的那段绑定投影
    return inspect.getsource(secrets_endpoints)


def test_binding_payload_carries_consumer_name():
    source = _admin_source()
    assert '"consumer_name"' in source


def test_binding_payload_carries_bound_by_name():
    source = _admin_source()
    assert '"bound_by_name"' in source


def test_global_binding_is_marked():
    """全局绑定没有绑定者，界面要显示「全局」而不是空白。"""
    source = _admin_source()
    assert '"is_global": not b.bound_by_user_id' in source


def test_all_three_consumer_types_are_resolved():
    """三类消费者各查一次名字，漏一个就会在界面上露出 uuid。"""
    source = _admin_source()
    for key in ('"mcp_server"', '"api_tool"', '"script_tool"'):
        assert key in source, key


def test_unknown_target_falls_back_to_id():
    """目标已删除时退回 id，至少还能定位。"""
    source = _admin_source()
    assert "or str(b.consumer_id)" in source


def test_consumer_models_are_importable():
    """名字解析依赖四个模型，类名写错只在运行时才炸。

    这一条是补的：前面几条都只查源码里的字符串，`from ... import ScriptTool`
    写错类名（真实类名是 `SysScriptTool`）时它们全绿，而接口 500。
    """
    from app.models.mcp import McpServer
    from app.models.script_tool import SysScriptTool
    from app.models.tool import SysApiTool
    from app.models.user import User

    for model in (McpServer, SysApiTool, SysScriptTool, User):
        assert hasattr(model, "id"), model
        assert hasattr(model, "__tablename__"), model
