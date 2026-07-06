from types import SimpleNamespace

import pytest

from app.services.ai.tool_nudge_policy import (
    STRONG_FORCE_SCORE,
    resolve_tool_nudge,
    should_consider_tool_nudge,
)
from app.services.ai.intent_service import IntentType

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


def test_sub_agent_call_nudge_for_data_query_uses_capability_target():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge(
        "帮我查一下设备资产列表",
        tools,
        available_sub_agent_names={"biz-data-agent", "knowledge-base"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='biz-data-agent'" in nudge.message
    assert "agent_name='chat-bi'" not in nudge.message
    assert nudge.should_force_first_call is True


def test_general_semantic_company_info_prefers_web_tool_over_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("web_search_baidu", "联网搜索公司信息、官网、新闻和最新资讯"),
    ]

    nudge = resolve_tool_nudge(
        "查一下有孚网络公司信息",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
        semantic_intent=IntentType.GENERAL,
        semantic_confidence=0.92,
    )

    assert nudge is not None
    assert nudge.tool_name == "web_search_baidu"
    assert nudge.should_force_first_call is False


def test_misclassified_company_info_does_not_force_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("web_search_baidu", "联网搜索公司信息、官网、新闻和最新资讯"),
    ]

    nudge = resolve_tool_nudge(
        "查一下有孚网络公司信息",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.93,
    )

    assert nudge is not None
    assert nudge.tool_name == "web_search_baidu"
    assert nudge.should_force_first_call is False


def test_public_news_query_prefers_web_tool_over_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("web_search_baidu", "联网搜索公司信息、官网、新闻和最新资讯"),
    ]

    nudge = resolve_tool_nudge(
        "查一下有孚网络最新新闻",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
    )

    assert nudge is not None
    assert nudge.tool_name == "web_search_baidu"


def test_ambiguous_lookup_does_not_force_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "查一下 abc",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
    )

    assert nudge is None


def test_data_query_semantic_forces_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("web_search_baidu", "联网搜索公司信息、官网、新闻和最新资讯"),
    ]

    nudge = resolve_tool_nudge(
        "查一下客户订单列表",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.91,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='biz-data-agent'" in nudge.message
    assert nudge.should_force_first_call is True


def test_server_load_query_prefers_shell_tool_over_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("exec_command", "在服务器上执行 shell 命令，查看服务器负载、CPU、内存、磁盘和进程状态"),
    ]

    nudge = resolve_tool_nudge(
        "查一下我机器的服务器负载情况",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_targets_by_capability={"data_query": "biz-data-agent"},
    )

    assert nudge is not None
    assert nudge.tool_name == "exec_command"
    assert nudge.should_force_first_call is False


def test_sub_agent_call_nudge_skips_when_target_agent_unavailable():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge(
        "帮我查一下设备资产列表",
        tools,
        available_sub_agent_names={"knowledge-base"},
    )
    assert nudge is None


def test_sub_agent_call_nudge_for_knowledge_query():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge("我想查一下设备运维规范和操作指引", tools)
    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 0.95
    assert "knowledge-base" in nudge.message


def test_notification_keyword_nudge_for_dingtalk_send():
    tools = [
        _tool("system_http_request", "发送 HTTP 请求"),
        _tool("send_dingtalk_message", "发送钉钉群机器人消息，读取当前用户个人中心消息通知配置"),
    ]
    nudge = resolve_tool_nudge("整理天气早报，同时发送到钉钉中", tools)
    assert nudge is not None
    assert nudge.tool_name == "send_dingtalk_message"
    assert nudge.score >= STRONG_FORCE_SCORE
    assert nudge.recommended_force_mode() == "send_dingtalk_message"
