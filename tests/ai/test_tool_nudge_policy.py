from types import SimpleNamespace

import pytest

from app.services.ai.tool_nudge_policy import (
    STRONG_FORCE_SCORE,
    ToolNudge,
    is_automatic_delivery_context,
    looks_like_explicit_user_question_request,
    resolve_tool_nudge,
    resolve_evidence_tool_fallback_nudge,
    resolve_tool_nudge_plan,
    should_consider_tool_nudge,
)
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.tool_policy import ToolMetadata
from app.services.ai.intent_service import IntentType
from app.services.ai.request_decision import (
    RequestCapability,
    RequestDecision,
    RequestSource,
    resolve_request_decision,
)
from app.services.ai.turn_decision import TurnDecision
from app.services.ai.knowledge_catalog import AuthorizedKnowledgeCatalog, KnowledgeBaseCatalogItem

pytestmark = pytest.mark.no_infrastructure


def _tool(name: str, description: str = ""):
    return SimpleNamespace(name=name, description=description)


def _evidence_tool(name: str, description: str):
    return SimpleNamespace(
        name=name,
        description=description,
        permission_scope="read",
        source_type="generic_api",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
    )


def test_capability_only_query_does_not_force_an_evidence_tool():
    nudge = resolve_tool_nudge(
        "你们支持查天气吗？",
        [_evidence_tool("weather_lookup", "查询指定城市的实时天气和未来天气")],
    )

    assert nudge is None or nudge.force_first_call is False


def test_common_capability_question_variants_do_not_force_an_evidence_tool():
    tool = _evidence_tool("weather_lookup", "查询指定城市的实时天气和未来天气")

    assert resolve_tool_nudge("可以查天气吗？", [tool]) is None
    assert resolve_tool_nudge("能查天气吗？", [tool]) is None


def test_capability_question_starting_with_query_is_not_forced_to_call_tool():
    tool = _evidence_tool("weather_lookup", "查询指定城市的实时天气和未来天气")

    assert resolve_tool_nudge("查询天气工具支持哪些城市？", [tool]) is None


def test_capability_question_with_real_target_still_uses_tool():
    nudge = resolve_tool_nudge(
        "查询上海明天实时天气",
        [_evidence_tool("weather_lookup", "查询指定城市的实时天气和未来天气")],
    )

    assert nudge is not None
    assert nudge.tool_name == "weather_lookup"
    assert nudge.force_first_call is True


def test_capability_question_with_concrete_target_is_not_treated_as_meta_only():
    nudge = resolve_tool_nudge(
        "支持查询上海明天实时天气吗？",
        [_evidence_tool("weather_lookup", "查询指定城市的实时天气和未来天气")],
    )

    assert nudge is not None
    assert nudge.tool_name == "weather_lookup"
    assert nudge.force_first_call is True


def test_evidence_fallback_requires_the_higher_minimum_score():
    assert resolve_evidence_tool_fallback_nudge(
        "天气情况",
        [_evidence_tool("weather_lookup", "查询城市天气")],
    ) is None


def test_evidence_fallback_rejects_ambiguous_best_candidate():
    tools = [
        _evidence_tool("weather_a", "查询上海天气温度"),
        _evidence_tool("weather_b", "查询上海天气降雨"),
    ]

    assert resolve_evidence_tool_fallback_nudge("查询上海天气", tools) is None


def test_evidence_fallback_skips_capability_questions():
    assert resolve_evidence_tool_fallback_nudge(
        "有没有天气查询工具",
        [_evidence_tool("weather_lookup", "查询天气")],
    ) is None


def test_multi_tool_plan_requires_two_distinct_high_confidence_read_tools():
    tools = [
        _evidence_tool("weather_lookup", "查询上海明天天气"),
        _evidence_tool("train_lookup", "查询北京到上海的高铁车票"),
    ]

    plan = resolve_tool_nudge_plan(
        "查上海明天天气，并查北京到上海的高铁",
        tools,
    )

    assert plan is not None
    assert [contract.tool_name for contract in plan.evidence_contracts] == [
        "weather_lookup",
        "train_lookup",
    ]
    assert plan.primary.tool_name == "weather_lookup"


def test_ambiguous_multi_tool_query_does_not_create_a_partial_plan():
    assert resolve_tool_nudge_plan(
        "查天气并顺便看看情况",
        [_evidence_tool("weather_lookup", "查询天气")],
    ) is None


def _office_tools(*names):
    descriptions = {
        "word_document_read": "读取 Word 文档结构或内容",
        "word_document_write": "创建或修改 Word 文档并生成可下载文件",
        "excel_document_read": "读取 Excel 工作簿结构或单元格区域",
        "excel_document_write": "创建或修改 Excel 副本并生成可下载文件",
    }
    return [_tool(name, descriptions[name]) for name in names]


@pytest.mark.parametrize(
    ("query", "tool_name"),
    [
        ("读取这个 Word 文档的内容", "word_document_read"),
        ("帮我查看 Excel 的 A1:C10", "excel_document_read"),
        ("把刚才内容保存为 Word 文档并给我下载地址", "word_document_write"),
        ("把这些数据导出为 Excel 文件", "excel_document_write"),
    ],
)
def test_office_nudge_uses_deterministic_chinese_intent(query, tool_name):
    nudge = resolve_tool_nudge(query, _office_tools(
        "word_document_read",
        "word_document_write",
        "excel_document_read",
        "excel_document_write",
    ))

    assert nudge is not None
    assert nudge.tool_name == tool_name
    assert nudge.should_force_first_call is True


def test_office_explicit_tool_name_keeps_original_tool_name():
    nudge = resolve_tool_nudge(
        "请调用 word_document_write 保存这份内容",
        _office_tools("word_document_write"),
    )

    assert nudge is not None
    assert nudge.tool_name == "word_document_write"
    assert "word_document_write" in nudge.message
    assert nudge.should_force_first_call is True


def test_office_nudge_requires_the_target_tool_to_be_mounted():
    assert resolve_tool_nudge(
        "把内容保存为 Word 文档",
        _office_tools("excel_document_write"),
    ) is None


def test_existing_file_download_request_does_not_force_office_write():
    assert resolve_tool_nudge(
        "请给我这个已有 Word 文件的下载地址",
        _office_tools("word_document_write"),
    ) is None


def test_office_type_ambiguity_does_not_force_a_tool():
    assert resolve_tool_nudge(
        "请把这份文档保存并提供下载地址",
        _office_tools("word_document_write", "excel_document_write"),
    ) is None


@pytest.mark.parametrize(
    "query",
    [
        "无需调用 word_document_write",
        "请勿调用 word_document_write",
        "word_document_write 是什么工具？",
        "word_document_read 和 word_document_write 有什么区别？",
        "请处理这个 Word 文件",
    ],
)
def test_office_nudge_does_not_force_non_execution_or_ambiguous_requests(query):
    assert resolve_tool_nudge(
        query,
        _office_tools(
            "word_document_read",
            "word_document_write",
            "excel_document_read",
            "excel_document_write",
        ),
    ) is None


def test_ambiguous_office_reference_keeps_unrelated_generic_tool_available():
    nudge = resolve_tool_nudge(
        "请搜索 Word 文件中的关键字",
        _office_tools("word_document_write")
        + [_tool("Grep", "在文件内容中搜索关键字")],
    )

    assert nudge is not None
    assert nudge.tool_name == "Grep"


def test_explicit_multiple_tool_names_do_not_fallback_to_bound_tool():
    assert resolve_tool_nudge(
        "请调用 word_document_write 和 excel_document_write",
        _office_tools("word_document_write"),
    ) is None


def test_mixed_office_read_write_request_is_not_forced_without_todo():
    assert resolve_tool_nudge(
        "先读取 Word 文档，再保存为 Word 文档",
        _office_tools("word_document_read", "word_document_write"),
    ) is None


def test_mixed_office_read_write_request_keeps_todo_priority_when_available():
    nudge = resolve_tool_nudge(
        "先读取 Word 文档，再保存为 Word 文档",
        _office_tools("word_document_read", "word_document_write")
        + [_tool("todo_write", "创建和更新任务清单")],
    )

    assert nudge is not None
    assert nudge.tool_name == "todo_write"
    assert nudge.should_force_first_call is True


@pytest.mark.parametrize(
    "query",
    [
        "不要保存为 Word 文档",
        "不需要保存 Word 文档",
        "无需生成 Excel 文件",
    ],
)
def test_negated_office_write_request_does_not_force_write(query):
    assert resolve_tool_nudge(
        query,
        _office_tools("word_document_write", "excel_document_write"),
    ) is None


def test_colloquial_read_request_selects_office_read_tool():
    nudge = resolve_tool_nudge(
        "帮我读一下这个 Excel 文件",
        _office_tools("excel_document_read"),
    )

    assert nudge is not None
    assert nudge.tool_name == "excel_document_read"
    assert nudge.should_force_first_call is True


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


def test_generic_read_only_mcp_evidence_tool_forces_first_call():
    tool = SimpleNamespace(
        name="mcp_get_tickets",
        description="查询明天高铁票的车次和票价",
        source_type="mcp",
        permission_scope="read",
        evidence_types=frozenset({EvidenceType.EXTERNAL_TOOL}),
    )

    nudge = resolve_tool_nudge("查询明天高铁票", [tool])

    assert nudge is not None
    assert nudge.tool_name == "mcp_get_tickets"
    assert nudge.should_force_first_call is True
    assert nudge.metadata is not None
    assert nudge.metadata.nudge_mode == "evidence"


def test_high_relevance_recommends_specific_tool_force_mode():
    tools = [_tool("exec_command", "执行 shell 命令查看系统负载与内存占用")]
    nudge = resolve_tool_nudge("帮我看一下系统负载内存占用", tools)
    assert nudge is not None
    assert nudge.score >= STRONG_FORCE_SCORE
    assert nudge.recommended_force_mode() == "exec_command"


def test_low_relevance_evidence_nudge_still_targets_the_specific_tool():
    nudge = ToolNudge(
        tool_name="mcp_get_tickets",
        score=0.1,
        message="必须先取得本轮工具结果",
        force_first_call=True,
        metadata=ToolMetadata(nudge_mode="evidence"),
    )

    assert nudge.recommended_force_mode() == "mcp_get_tickets"


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


def test_platform_self_help_prefers_public_docs_host_file_tool_over_sub_agent():
    query = "那多智能体并行是什么意思啊，开了和不开有什么区别啊"
    decision = resolve_request_decision(
        query,
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.95,
    )
    tools = [
        _tool("sub_agent_call", "委派知识库助手查询内部手册"),
        _tool("Grep", "宿主机侧按关键字搜索公共 docs Markdown 文档"),
        _tool("Read", "宿主机侧读取公共 docs Markdown 文档"),
    ]

    nudge = resolve_tool_nudge(
        query,
        tools,
        request_decision=decision,
        available_sub_agent_names={"knowledge-base"},
        sub_agent_candidates_by_capability={"knowledge_base": ["knowledge-base"]},
    )

    assert nudge is not None
    assert nudge.tool_name == "Grep"
    assert nudge.should_force_first_call is True
    assert "公共 docs" in nudge.message
    assert "Bash" in nudge.message
    assert "/app/*.md" in nudge.message
    assert "禁止递归扫描 /app" in nudge.message


@pytest.mark.parametrize(
    "query",
    [
        "随便问我几个问题",
        "考考我",
        "测测我",
        "请开始提问",
        "出题考我",
        "ask me a few questions",
        "请你问我几个问题",
        "采访我一下，帮我梳理需求",
        "我不知道怎么提问，你引导我",
        "先问我几个问题再帮我规划",
        "一个一个问我，不要一次问完",
        "通过提问了解一下我的需求",
    ],
)
def test_explicit_interactive_question_requests_are_detected(query):
    assert looks_like_explicit_user_question_request(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "给我列几个问题",
        "不要问我，直接回答",
        "不用提问，直接给结论",
        "【用户回答】\ninteraction_type: question\nquestion_id: uq_1",
    ],
)
def test_question_listing_negation_and_receipts_are_not_interactive_requests(query):
    assert looks_like_explicit_user_question_request(query) is False


def test_explicit_interactive_request_forces_ask_user_question_when_tool_is_available():
    nudge = resolve_tool_nudge(
        "随便问我几个问题",
        [_tool("ask_user_question", "向用户展示选项提问并等待回答")],
    )

    assert nudge is not None
    assert nudge.tool_name == "ask_user_question"
    assert nudge.score == 1.0
    assert nudge.should_force_first_call is True
    assert "明确要求互动式提问" in nudge.message


def test_short_explicit_interactive_request_bypasses_generic_nudge_length_gate():
    nudge = resolve_tool_nudge(
        "考考我",
        [_tool("ask_user_question", "向用户展示选项提问并等待回答")],
    )

    assert nudge is not None
    assert nudge.tool_name == "ask_user_question"


@pytest.mark.parametrize(
    "query",
    [
        "【自动化指令】定时任务：随便问我几个问题",
        "后台自动任务：考考我",
        "quick_suggestions_forbidden=true；随便问我几个问题",
        "【自动化指令任务内容：考考我",
        "TaskCenter 自动任务：随便问我几个问题",
    ],
)
def test_automatic_context_does_not_trigger_interactive_question(query):
    assert resolve_tool_nudge(
        query,
        [_tool("ask_user_question", "向用户展示选项提问并等待回答")],
    ) is None


def test_automatic_delivery_flags_disable_explicit_question_nudge():
    tools = [_tool("ask_user_question", "向用户展示选项提问并等待回答")]

    assert is_automatic_delivery_context({"is_scheduled_task": True}) is True
    assert resolve_tool_nudge(
        "考考我",
        tools,
        exclude_tools={"ask_user_question"},
        allow_explicit_question=False,
    ) is None


def test_explicit_interactive_request_does_not_nudge_without_question_tool():
    assert resolve_tool_nudge("随便问我几个问题", [_tool("Bash", "执行命令")]) is None


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


def test_multi_step_request_prioritizes_todo_write_before_semantic_data_delegation():
    tools = [
        _tool("todo_write", "记录和更新多步骤任务的结构化执行清单"),
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "请先查询销售数据集，再按区域汇总，最后生成一份 Excel 报告",
        tools,
        available_sub_agent_names={"chat-bi"},
        sub_agent_candidates_by_capability={"data_query": ["chat-bi"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
    )

    assert nudge is not None
    assert nudge.tool_name == "todo_write"
    assert nudge.should_force_first_call is True
    assert "todo_write" in nudge.message
    assert "继续执行" in nudge.message


def test_single_step_data_query_still_prioritizes_sub_agent_call():
    tools = [
        _tool("todo_write", "记录和更新多步骤任务的结构化执行清单"),
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "帮我查一下设备资产列表",
        tools,
        available_sub_agent_names={"chat-bi"},
        sub_agent_candidates_by_capability={"data_query": ["chat-bi"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"


def test_generic_multi_step_request_prioritizes_todo_write_without_data_intent():
    tools = [
        _tool("todo_write", "记录和更新多步骤任务的结构化执行清单"),
        _tool("Read", "读取文件内容"),
        _tool("Bash", "执行命令并运行测试"),
    ]

    nudge = resolve_tool_nudge(
        "先读取 README，再修改配置，最后运行测试",
        tools,
    )

    assert nudge is not None
    assert nudge.tool_name == "todo_write"
    assert nudge.should_force_first_call is True


def test_explicit_sub_agent_request_keeps_priority_over_todo_write():
    tools = [
        _tool("todo_write", "记录和更新多步骤任务的结构化执行清单"),
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "先调用 chat-bi 查询销售数据，再生成 Excel 报告",
        tools,
        available_sub_agent_names={"chat-bi"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert "agent_name='chat-bi'" in nudge.message


def test_current_user_profile_nudge_precedes_data_sub_agent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("get_myinfo", "读取当前登录用户本人的基本信息、部门、组织路径、扩展信息、角色和权限"),
    ]

    nudge = resolve_tool_nudge(
        "看看我的详细信息",
        tools,
        available_sub_agent_names={"data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.99,
    )

    assert nudge is not None
    assert nudge.tool_name == "get_myinfo"
    assert nudge.should_force_first_call is True
    assert "必须先调用 sub_agent_call" not in nudge.message


def test_business_data_query_does_not_use_current_user_profile_nudge():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("get_myinfo", "读取当前登录用户本人的基本信息、部门、组织路径、扩展信息、角色和权限"),
    ]

    nudge = resolve_tool_nudge(
        "看看我的订单详细信息",
        tools,
        available_sub_agent_names={"data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["data-agent"]},
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.99,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"


def test_sub_agent_call_is_not_selected_by_generic_tool_relevance_without_intent():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge("帮我查一下设备资产列表", tools)

    assert nudge is None


def test_weak_catalog_evidence_nudges_one_direct_knowledge_search_not_sub_agent():
    decision = resolve_request_decision(
        "查看春秋航空9C6475航班的准点率和退改签政策",
        semantic_intent=IntentType.KNOWLEDGE_BASE,
        semantic_confidence=0.9,
        knowledge_catalog=AuthorizedKnowledgeCatalog(
            status="available",
            items=(
                KnowledgeBaseCatalogItem(
                    ragflow_dataset_id="kb-ev",
                    name="蔚来汽车知识库",
                    description="车辆功能和换电操作说明",
                ),
            ),
        ),
    )
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务"),
        _tool("search_knowledge_base", "检索授权知识库文档正文并返回引用"),
    ]

    nudge = resolve_tool_nudge(
        "查看春秋航空9C6475航班的准点率和退改签政策",
        tools,
        available_sub_agent_names={"knowledge-agent"},
        sub_agent_candidates_by_capability={"knowledge_base": ["knowledge-agent"]},
        request_decision=decision,
    )

    assert nudge is not None
    assert nudge.tool_name == "search_knowledge_base"
    assert nudge.should_force_first_call is True


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


def test_tool_nudge_can_reuse_turn_decision_without_reclassifying_query():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]
    turn_decision = TurnDecision(
        source="internal_structured_data",
        capability="data_query",
        should_delegate=True,
        delegate_capability="data_query",
        allows_data_route=True,
        chatbi_mode="direct",
    )

    nudge = resolve_tool_nudge(
        "查询本月订单金额",
        tools,
        available_sub_agent_names={"biz-data-agent"},
        sub_agent_candidates_by_capability={"data_query": ["biz-data-agent"]},
        turn_decision=turn_decision,
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"


def test_tool_nudge_attaches_neutral_or_registered_metadata():
    nudge = resolve_tool_nudge(
        "帮我看一下系统负载",
        [_tool("exec_command", "执行 shell 命令查看系统负载与内存占用")],
    )

    assert nudge is not None
    assert nudge.metadata is not None
    assert nudge.metadata.capability == "runtime_tool"


def test_explicit_multi_sub_agents_nudges_batch_call():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("sub_agent_batch_call", "并行委派 1-4 个彼此独立的子智能体任务"),
    ]

    nudge = resolve_tool_nudge(
        "调用 chat-bi 和 knowledge-base 子代理查一下设备和手册",
        tools,
        available_sub_agent_names={"chat-bi", "knowledge-base"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_batch_call"
    assert nudge.score == 1.0
    assert nudge.should_force_first_call is True
    assert "'chat-bi'" in nudge.message
    assert "'knowledge-base'" in nudge.message
    assert "sub_agent_batch_call" in nudge.message


def test_parallel_keyword_with_sub_agents_nudges_batch_call():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("sub_agent_batch_call", "并行委派 1-4 个彼此独立的子智能体任务"),
    ]

    nudge = resolve_tool_nudge(
        "你并行同时调用一下知识库和数据查询智能体呢",
        tools,
        available_sub_agent_names={"知识库", "数据查询"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_batch_call"
    assert nudge.score == 1.0
    assert nudge.should_force_first_call is True
    assert "'知识库'" in nudge.message
    assert "'数据查询'" in nudge.message


def test_single_sub_agent_with_parallel_keyword_nudges_batch_call():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
        _tool("sub_agent_batch_call", "并行委派 1-4 个彼此独立的子智能体任务"),
    ]

    nudge = resolve_tool_nudge(
        "并行调用知识库助手",
        tools,
        available_sub_agent_names={"知识库助手"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_batch_call"
    assert nudge.score == 1.0
    assert nudge.should_force_first_call is True
    assert "'知识库助手'" in nudge.message


def test_multi_sub_agents_fallback_to_single_when_batch_tool_missing():
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    nudge = resolve_tool_nudge(
        "调用 chat-bi 和 knowledge-base 子代理",
        tools,
        available_sub_agent_names={"chat-bi", "knowledge-base"},
    )

    assert nudge is not None
    assert nudge.tool_name == "sub_agent_call"
    assert nudge.score == 1.0
    assert "agent_name='chat-bi'" in nudge.message


def test_todo_and_task_list_does_not_nudge_data_sub_agent():
    """查看任务列表、待办列表等请求绝不强推数据查询子代理。"""
    tools = [
        _tool("sub_agent_call", "委派其他专有子智能体执行特定任务（如查数、查手册等）"),
    ]

    for q in ("看看我的任务列表", "查看待办事项清单", "我的待办有哪些", "todo list"):
        nudge = resolve_tool_nudge(
            q,
            tools,
            available_sub_agent_names={"data-agent"},
            sub_agent_candidates_by_capability={"data_query": ["data-agent"]},
            semantic_intent=IntentType.DATA_QUERY,
            semantic_confidence=0.9,
        )

        assert nudge is None
