"""工具调用超时下发的单测。

`run_config` 在请求入口只能下发全局值（那时还不知道是哪个智能体版本）；
真正的「版本级优先、否则全局」要等 runner 解析完工具才确定，所以 runner 会
补发一次 `run_config` 覆盖。这里锁住取值口径，避免以后改成「读第一个工具的
timeout_seconds 就行」时把边界条件丢掉。
"""
from types import SimpleNamespace

from app.services.ai.runners.assistant_agent_runner import _first_tool_timeout_seconds
from app.services.ai.runtime.agentscope.tool_timeout import (
    resolve_agent_toolcall_timeout,
)


def test_reads_first_positive_timeout():
    tools = [
        SimpleNamespace(timeout_seconds=300.0),
        SimpleNamespace(timeout_seconds=180.0),
    ]
    assert _first_tool_timeout_seconds(tools) == 300.0


def test_skips_missing_zero_and_bool():
    tools = [
        SimpleNamespace(timeout_seconds=None),
        SimpleNamespace(timeout_seconds=0),
        SimpleNamespace(timeout_seconds=True),
        SimpleNamespace(timeout_seconds=90),
    ]
    assert _first_tool_timeout_seconds(tools) == 90.0


def test_returns_none_without_tools():
    assert _first_tool_timeout_seconds([]) is None
    assert _first_tool_timeout_seconds(None) is None


def test_version_level_timeout_wins_over_global():
    # 「配置了按智能体、没配置按全局」——前端分母必须跟着这个口径走
    assert resolve_agent_toolcall_timeout(180, 300) == 300.0
    assert resolve_agent_toolcall_timeout(180, None) == 180.0
    # 版本级非法值不生效，回退全局
    assert resolve_agent_toolcall_timeout(180, "abc") == 180.0
