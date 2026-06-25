from types import SimpleNamespace

import pytest

from app.services.ai.tool_nudge_policy import (
    STRONG_FORCE_SCORE,
    resolve_tool_nudge,
    should_consider_tool_nudge,
)

pytestmark = pytest.mark.no_infrastructure


def _tool(name: str, description: str = ""):
    return SimpleNamespace(name=name, description=description)


def test_nudges_tool_by_description_relevance():
    # 候选完全来自工具 name+description 与问题的字符重叠，不依赖写死的工具名/类别。
    tools = [
        _tool("exec_command", "在服务器上执行 shell 命令，查看系统负载、CPU 和内存占用"),
        _tool("memory_search", "检索用户长期记忆"),
    ]
    nudge = resolve_tool_nudge("帮我看一下系统负载", tools)
    assert nudge is not None
    assert nudge.tool_name == "exec_command"
    assert "exec_command" in nudge.message


def test_high_relevance_recommends_specific_tool_force_mode():
    tools = [_tool("exec_command", "执行 shell 命令查看系统负载与内存占用")]
    nudge = resolve_tool_nudge("帮我看一下系统负载内存占用", tools)
    assert nudge is not None
    assert nudge.score >= STRONG_FORCE_SCORE
    assert nudge.recommended_force_mode() == "exec_command"


def test_no_nudge_when_no_tool_is_relevant():
    tools = [_tool("memory_search", "检索用户长期记忆")]
    nudge = resolve_tool_nudge("帮我看一下系统负载", tools)
    assert nudge is None


def test_grep_like_tool_nudged_for_log_search():
    tools = [
        _tool("Grep", "在文件或日志中按正则搜索匹配的报错堆栈内容"),
        _tool("Read", "读取文件内容"),
    ]
    nudge = resolve_tool_nudge("帮我在日志里搜一下报错堆栈", tools)
    assert nudge is not None
    assert nudge.tool_name == "Grep"


def test_excluded_tools_are_never_nudged():
    # 记忆/写入类工具被排除，即便描述高度相关也不促发。
    tools = [_tool("memory_search", "检索系统负载历史记忆与系统负载记录")]
    assert resolve_tool_nudge("帮我看一下系统负载", tools) is None


def test_greeting_is_not_nudged():
    assert should_consider_tool_nudge("你好") is False
    assert resolve_tool_nudge("你好", [_tool("Bash", "执行命令")]) is None


def test_plain_chat_does_not_nudge():
    tools = [
        _tool("Bash", "执行 shell 命令"),
        _tool("Grep", "搜索文件内容"),
    ]
    nudge = resolve_tool_nudge("帮我把这段话润色一下", tools)
    assert nudge is None


def test_sub_agent_call_nudge_for_data_query():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("exec_command", "在服务器上执行 shell 命令")
    ]
    nudge = resolve_tool_nudge("帮我查一下设备资产列表", tools)
    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 0.95
    assert "chat-bi" in nudge.message


def test_sub_agent_call_nudge_for_knowledge_query():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge("我想查一下设备运维规范和操作指引", tools)
    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 0.95
    assert "knowledge-base" in nudge.message
