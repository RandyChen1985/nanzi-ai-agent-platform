from types import SimpleNamespace

import pytest

from app.services.ai.tool_nudge_policy import (
    STRONG_FORCE_SCORE,
    resolve_tool_nudge,
    should_consider_tool_nudge,
)
from app.services.ai.intent_service import IntentType
from app.services.ai.request_decision import (
    RequestCapability,
    RequestDecision,
    RequestSource,
    resolve_request_decision,
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
    nudge = resolve_tool_nudge(
        "帮我查一下设备资产列表",
        tools,
        available_sub_agent_names={"chat-bi", "finance-expense"},
        sub_agent_candidates_by_capability={
            "data_query": ["chat-bi", "finance-expense"],
        },
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
    )
    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 0.95
    assert nudge.should_force_first_call is True
    assert "sub_agent_call" in nudge.message
    assert "语义" in nudge.message or "自动路由" in nudge.message
    assert "agent_name='chat-bi'" not in nudge.message
    assert "agent_name='finance-expense'" not in nudge.message
    assert "`chat-bi`" in nudge.message
    assert "`finance-expense`" in nudge.message


def test_sub_agent_call_is_not_selected_by_generic_tool_relevance_without_intent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge("帮我查一下设备资产列表", tools)

    assert nudge is None


@pytest.mark.parametrize(
    "query",
    [
        "如何安装技能呢",
        "技能怎么挂载",
        "agent 怎么配置",
        "工具怎么启用",
        "插件如何安装",
        "模型在哪里配置呢",
    ],
)
def test_platform_self_service_query_does_not_delegate_to_sub_agent(query):
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        query,
        tools,
        available_sub_agent_names={"chat-bi", "knowledge-base"},
    )

    assert nudge is None


def test_explicit_sub_agent_name_forces_delegation():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "调用 chat-bi 子代理查一下设备资产列表",
        tools,
        available_sub_agent_names={"chat-bi", "knowledge-base"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 1.0
    assert "agent_name='chat-bi'" in nudge.message
    assert nudge.should_force_first_call is True


def test_explicit_sub_agent_alias_matches_available_name():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "让 knowledge_base 智能体处理一下这段输入",
        tools,
        available_sub_agent_names={"knowledge-base"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='knowledge-base'" in nudge.message
    assert nudge.should_force_first_call is True


def test_explicit_sub_agent_name_skips_when_unavailable():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "调用 unknown 子代理处理一下",
        tools,
        available_sub_agent_names={"chat-bi", "knowledge-base"},
    )

    assert nudge is None


def test_sub_agent_call_nudge_for_data_query_uses_capability_candidates_semantically():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge(
        "帮我查一下设备资产列表",
        tools,
        available_sub_agent_names={"biz-data-agent", "knowledge-base"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='biz-data-agent'" not in nudge.message
    assert "`biz-data-agent`" in nudge.message
    assert "data_query" in nudge.message
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
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
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
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
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
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
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
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
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
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.91,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='biz-data-agent'" not in nudge.message
    assert "`biz-data-agent`" in nudge.message
    assert nudge.should_force_first_call is True


def test_data_query_intent_forces_sub_agent_without_strong_keyword():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "查询合同编号 YVPR-FZN-202211-068 下的所有资产信息",
        tools,
        available_sub_agent_names={"chat-bi"},
        sub_agent_candidates_by_capability={"data_query": ["chat-bi"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.9,
        turn_intent=IntentType.DATA_QUERY,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='chat-bi'" not in nudge.message
    assert "`chat-bi`" in nudge.message
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
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
    )

    assert nudge is not None
    assert nudge.tool_name == "exec_command"
    assert nudge.should_force_first_call is False


def test_runtime_diagnostic_data_intent_does_not_force_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "查看当前系统的CPU和内存使用情况",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        turn_intent=IntentType.DATA_QUERY,
    )

    assert nudge is None


def test_canonical_runtime_decision_blocks_chatbi_sub_agent_when_turn_is_misclassified():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    request_decision = resolve_request_decision(
        "查询一下我机器的负载情况",
        turn_intent=IntentType.DATA_QUERY,
    )

    nudge = resolve_tool_nudge(
        "查询一下我机器的负载情况",
        tools,
        available_sub_agent_names={"chat-bi"},
        sub_agent_candidates_by_capability={"data_query": ["chat-bi"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.9,
        turn_intent=IntentType.DATA_QUERY,
        request_decision=request_decision,
    )

    assert request_decision.capability.value == "runtime_tool"
    assert request_decision.allows_data_route is False
    assert nudge is None


def test_generic_data_intent_forces_data_sub_agent_without_business_signal():
    """意图已是 DATA_QUERY 时，即使没有强业务关键词也强制委派。"""
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "查一下 abc 的状态",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.91,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='biz-data-agent'" not in nudge.message
    assert "`biz-data-agent`" in nudge.message
    assert nudge.should_force_first_call is True


@pytest.mark.parametrize(
    "query",
    [
        "苹果手机销量趋势",
        "帮我统计一下这段文本字数",
        "Python list 是什么意思",
    ],
)
def test_general_semantic_query_does_not_delegate_by_keywords_only(query):
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        query,
        tools,
        available_sub_agent_names={"chat-bi", "knowledge-base"},
        sub_agent_candidates_by_capability={
            "data_query": ["chat-bi"],
            "knowledge_base": ["knowledge-base"],
        },
        semantic_intent=IntentType.GENERAL,
        semantic_confidence=0.95,
    )

    assert nudge is None


def test_knowledge_base_semantic_query_still_forces_knowledge_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "查一下设备运维规范",
        tools,
        available_sub_agent_names={"knowledge-base"},
        sub_agent_candidates_by_capability={"knowledge_base": ["knowledge-base"]},
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.92,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='knowledge-base'" not in nudge.message
    assert "`knowledge-base`" in nudge.message
    assert nudge.should_force_first_call is True


def test_resource_catalog_query_does_not_force_knowledge_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("list_accessible_knowledge_bases", "列出当前用户有权限的知识库目录"),
        _tool("search_knowledge_base", "知识库文档检索"),
    ]

    nudge = resolve_tool_nudge(
        "我们有哪些知识库权限",
        tools,
        available_sub_agent_names={"knowledge-base"},
        sub_agent_candidates_by_capability={"knowledge_base": ["knowledge-base"]},
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.9,
    )

    assert nudge is None or nudge.tool_name != "sub_agent_call"
    if nudge is not None:
        assert nudge.tool_name == "list_accessible_knowledge_bases"


def test_general_previous_web_info_visualization_does_not_force_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "能不能把刚刚的信息可视化一下呢",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        semantic_intent=IntentType.GENERAL,
        semantic_confidence=0.95,
    )

    assert nudge is None


def test_data_query_previous_result_visualization_still_forces_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "把刚才的结果画成柱状图",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.93,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='biz-data-agent'" not in nudge.message
    assert "`biz-data-agent`" in nudge.message
    assert nudge.should_force_first_call is True


def test_runtime_diagnostic_data_intent_prefers_shell_tool():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("exec_command", "在服务器上执行 shell 命令，查看系统负载、CPU、内存、磁盘和进程状态"),
    ]

    nudge = resolve_tool_nudge(
        "查看当前系统的CPU和内存使用情况",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        turn_intent=IntentType.DATA_QUERY,
    )

    assert nudge is not None
    assert nudge.tool_name == "exec_command"
    assert nudge.should_force_first_call is False


def test_chatbi_denied_source_does_not_force_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    decision = RequestDecision(
        source=RequestSource.INTERNAL_STRUCTURED_DATA,
        capability=RequestCapability.DATA_QUERY,
        confidence=0.9,
        reasoning="动作词触发了旧的 DATA_QUERY 语义，但来源是本机文件",
        chatbi_mode="deny",
        chatbi_evidence_level="source_conflict",
        allows_data_route=False,
        should_delegate=False,
    )

    nudge = resolve_tool_nudge(
        "统计一下我机器的文件数",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        request_decision=decision,
    )

    assert nudge is None


def test_chatbi_clarify_source_does_not_force_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    decision = RequestDecision(
        source=RequestSource.INTERNAL_STRUCTURED_DATA,
        capability=RequestCapability.DATA_QUERY,
        confidence=0.9,
        reasoning="只有业务语义，尚无数据集证据",
        chatbi_mode="clarify",
        chatbi_evidence_level="semantic_only",
        allows_data_route=True,
        should_delegate=False,
    )

    nudge = resolve_tool_nudge(
        "统计一下客户订单",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        request_decision=decision,
    )

    assert nudge is None


def test_sub_agent_call_nudge_skips_when_target_agent_unavailable():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge(
        "帮我查一下设备资产列表",
        tools,
        available_sub_agent_names={"knowledge-base"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
    )
    assert nudge is None

def test_sub_agent_call_nudge_for_knowledge_query():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    nudge = resolve_tool_nudge(
        "我想查一下设备运维规范和操作指引",
        tools,
        available_sub_agent_names={"knowledge-base"},
        sub_agent_candidates_by_capability={"knowledge_base": ["knowledge-base"]},
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.92,
    )
    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 0.95
    assert "agent_name='knowledge-base'" not in nudge.message
    assert "`knowledge-base`" in nudge.message
    assert ("语义" in nudge.message) or ("自动路由" in nudge.message)

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
