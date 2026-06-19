import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.turn_classifier import (
    TurnClassification,
    TurnType,
    adapt_classification_for_agent,
    classify_turn_heuristic,
    classify_turn_from_intent,
    resolve_turn_for_session,
    turn_type_label,
    should_inject_user_context,
)
from app.services.ai.intent_service import IntentResponse, IntentType
from app.services.ai.data_query_turn_classifier import (
    DataQueryTurnType,
    data_query_turn_type_label,
    resolve_data_query_turn_classification,
)


pytestmark = pytest.mark.no_infrastructure


def _mock_chat_client(content: str):
    chat_client = AsyncMock()
    chat_client.generate_text.return_value = content
    return chat_client


@pytest.mark.parametrize(
    "query,expected",
    [
        ("把这个流程保存为技能", TurnType.META_ACTION),
        ("保存这个结果", TurnType.CONTEXT_ACTION),
        ("使用用户列表查询技能", TurnType.SKILL_EXECUTION),
        ("查询用户列表并可视化分析", TurnType.DATA_QUERY_REQUEST),
        ("高温告警的标准处理流程是什么", TurnType.KNOWLEDGE),
    ],
)
def test_classify_turn_heuristic_fixed_cases(query, expected):
    result = classify_turn_heuristic(query, can_do_data=True, has_last_data_result=False)
    assert result is not None
    assert result.turn_type == expected


@pytest.mark.parametrize(
    "query",
    [
        "联网搜索一下有孚网络的最新信息",
        "上网查查这家公司的最新新闻",
        "百度一下今天的科技资讯",
        "帮我搜索一下 GPT 的最新进展",
        "有孚网络最新消息",
        "搜索有孚网络的最新信息",
    ],
)
def test_web_search_query_routes_to_general(query):
    result = classify_turn_heuristic(query, can_do_data=False, has_last_data_result=False)
    assert result is not None
    assert result.turn_type == TurnType.GENERAL
    assert result.intent == IntentType.GENERAL


@pytest.mark.parametrize(
    "query",
    [
        "查一下目前的库存",
        "查一下现在有多少告警",
        "统计本月的订单数量",
    ],
)
def test_data_query_not_misrouted_as_web_search(query):
    # 查数语句不应被联网搜索启发式短路（仍交给意图 LLM 处理）
    result = classify_turn_heuristic(query, can_do_data=True, has_last_data_result=False)
    assert result is None or result.turn_type != TurnType.GENERAL


def test_internal_sop_still_classified_as_knowledge():
    result = classify_turn_heuristic(
        "高温告警的标准处理流程是什么",
        can_do_data=False,
        has_last_data_result=False,
    )
    assert result is not None
    assert result.turn_type == TurnType.KNOWLEDGE


def test_classify_turn_from_intent_web_search_overrides_knowledge_without_binding():
    intent = IntentResponse(
        intent=IntentType.KNOWLEDGE_BASE,
        confidence=0.8,
        reasoning="用户想查有孚网络的最新信息",
        entities=["有孚网络"],
    )
    result = classify_turn_from_intent(
        intent,
        can_do_data=False,
        user_query="搜索一下有孚网络的最新信息",
        has_knowledge_binding=False,
    )
    assert result.turn_type == TurnType.GENERAL
    assert result.intent == IntentType.GENERAL


def test_classify_turn_from_intent_keeps_knowledge_when_bound():
    intent = IntentResponse(
        intent=IntentType.KNOWLEDGE_BASE,
        confidence=0.8,
        reasoning="用户想查最新信息",
        entities=[],
    )
    result = classify_turn_from_intent(
        intent,
        can_do_data=False,
        user_query="搜索一下最新信息",
        has_knowledge_binding=True,
    )
    assert result.turn_type == TurnType.KNOWLEDGE


@pytest.mark.parametrize(
    "query",
    [
        "你好",
        "您好",
        "hello",
        "你是谁",
        "谢谢",
    ],
)
def test_greeting_routes_to_general_without_intent_llm(query):
    result = classify_turn_heuristic(query, can_do_data=False, has_last_data_result=False)
    assert result is not None
    assert result.turn_type == TurnType.GENERAL
    assert result.skip_intent_llm is True
    assert result.intent == IntentType.GENERAL


@pytest.mark.parametrize(
    "query",
    [
        "你好，查一下机房列表",
        "您好，高温告警处理流程是什么",
    ],
)
def test_greeting_compound_not_short_circuited(query):
    result = classify_turn_heuristic(query, can_do_data=False, has_last_data_result=False)
    assert result is None or not (
        result.skip_intent_llm and result.reasoning and "问候" in result.reasoning
    )


def test_reuse_previous_result_requires_cached_result():
    result = classify_turn_heuristic(
        "把刚才的结果画成柱状图",
        can_do_data=True,
        has_last_data_result=False,
    )
    assert result is None

    result = classify_turn_heuristic(
        "把刚才的结果画成柱状图",
        can_do_data=True,
        has_last_data_result=True,
    )
    assert result.turn_type == TurnType.DATA_QUERY_REQUEST
    assert result.skip_intent_llm is True


def test_classify_turn_from_intent_data_query():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.9,
        reasoning="查业务数据",
        entities=[],
    )
    result = classify_turn_from_intent(intent, can_do_data=True)
    assert result.turn_type == TurnType.DATA_QUERY_REQUEST


def test_classify_turn_from_intent_knowledge():
    intent = IntentResponse(
        intent=IntentType.KNOWLEDGE_BASE,
        confidence=0.88,
        reasoning="问 SOP",
        entities=[],
    )
    result = classify_turn_from_intent(intent, can_do_data=True)
    assert result.turn_type == TurnType.KNOWLEDGE
    assert result.requires_knowledge_search is True


def test_classify_turn_heuristic_with_bound_knowledge_dataset_ids():
    rid = "4525d66cec7111f0a3d00242ac120006"
    result = classify_turn_heuristic(
        "换电过程中可以开门吗？",
        can_do_data=False,
        knowledge_dataset_ids=[rid],
    )
    assert result is not None
    assert result.turn_type == TurnType.KNOWLEDGE
    assert result.skip_intent_llm is True


@pytest.mark.asyncio
async def test_resolve_turn_for_session_skips_intent_llm_when_dataset_ids_bound():
    with patch(
        "app.services.ai.turn_classifier.intent_service.identify_intent",
        AsyncMock(),
    ) as mock_identify:
        classification, intent_info, elapsed_ms = await resolve_turn_for_session(
            "换电过程中可以开门吗？",
            [{"role": "user", "content": "换电过程中可以开门吗？"}],
            can_do_data=False,
            knowledge_dataset_ids=["4525d66cec7111f0a3d00242ac120006"],
        )

    mock_identify.assert_not_awaited()
    assert classification.turn_type == TurnType.KNOWLEDGE
    assert intent_info is None
    assert elapsed_ms == 0.0


def test_classify_turn_from_intent_data_query_on_non_data_agent():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.9,
        reasoning="用户想查业务指标",
        entities=[],
    )
    result = classify_turn_from_intent(intent, can_do_data=False)
    assert result.turn_type == TurnType.GENERAL
    assert result.intent == IntentType.DATA_QUERY
    assert "无 data_query 能力" in result.reasoning


@pytest.mark.asyncio
async def test_resolve_turn_for_session_non_data_agent_uses_intent_llm_when_heuristic_misses():
    with patch(
        "app.services.ai.turn_classifier.intent_service.identify_intent",
        AsyncMock(
            return_value=IntentResponse(
                intent=IntentType.KNOWLEDGE_BASE,
                confidence=0.91,
                reasoning="用户在询问处理指引",
                entities=[],
            )
        ),
    ) as mock_identify:
        classification, intent_info, elapsed_ms = await resolve_turn_for_session(
            "机房温度过高时应该注意什么",
            [{"role": "user", "content": "机房温度过高时应该注意什么"}],
            can_do_data=False,
        )

    mock_identify.assert_awaited_once()
    assert classification.turn_type == TurnType.KNOWLEDGE
    assert classification.requires_knowledge_search is True
    assert classification.skip_intent_llm is False
    assert intent_info.intent == IntentType.KNOWLEDGE_BASE
    assert elapsed_ms >= 0


@pytest.mark.asyncio
async def test_resolve_turn_for_session_non_data_agent_skips_intent_llm_on_heuristic_hit():
    with patch(
        "app.services.ai.turn_classifier.intent_service.identify_intent",
        AsyncMock(),
    ) as mock_identify:
        classification, intent_info, elapsed_ms = await resolve_turn_for_session(
            "高温告警的标准处理流程是什么",
            [{"role": "user", "content": "高温告警的标准处理流程是什么"}],
            can_do_data=False,
        )

    mock_identify.assert_not_awaited()
    assert classification.turn_type == TurnType.KNOWLEDGE
    assert classification.skip_intent_llm is True
    assert intent_info is None
    assert elapsed_ms == 0.0


def test_classify_turn_from_intent_knowledge_non_data_agent():
    intent = IntentResponse(
        intent=IntentType.KNOWLEDGE_BASE,
        confidence=0.88,
        reasoning="问 SOP",
        entities=[],
    )
    result = classify_turn_from_intent(intent, can_do_data=False)
    assert result.turn_type == TurnType.KNOWLEDGE
    assert result.requires_knowledge_search is True


def test_turn_type_label():
    assert turn_type_label(TurnType.DATA_QUERY_REQUEST) == "数据查询请求"
    assert data_query_turn_type_label(DataQueryTurnType.REUSE_PREVIOUS_RESULT) == "复用上一轮结果"
    assert data_query_turn_type_label(DataQueryTurnType.CLARIFICATION_OR_NON_DATA) == "需澄清或非查数请求"


def test_shared_turn_classification_is_generic_not_chatbi_specific():
    classification = TurnClassification(
        turn_type=TurnType.DATA_QUERY_REQUEST,
        reasoning="数据查询请求",
        intent=IntentType.DATA_QUERY,
    )

    assert not hasattr(classification, "requires_fresh_data")
    assert not hasattr(classification, "requires_few_shot")
    assert not hasattr(classification, "use_data_executor")


def test_adapt_classification_for_non_data_agent():
    base = classify_turn_heuristic("查询用户列表", can_do_data=True, has_last_data_result=False)
    assert base is None or base.turn_type == TurnType.DATA_QUERY_REQUEST
    data_query = TurnClassification(
        turn_type=TurnType.DATA_QUERY_REQUEST,
        reasoning="test",
        intent=IntentType.DATA_QUERY,
    )
    adapted = adapt_classification_for_agent(data_query, can_do_data=False)
    assert adapted.turn_type == TurnType.DATA_QUERY_REQUEST


def test_should_inject_user_context():
    assert should_inject_user_context(TurnType.DATA_QUERY_REQUEST) is True
    assert should_inject_user_context(TurnType.SKILL_EXECUTION) is True
    assert should_inject_user_context(TurnType.CONTEXT_ACTION) is True


def test_should_inject_ltm_for_chatbi_turns():
    from app.services.ai.turn_classifier import should_inject_ltm

    assert should_inject_ltm(TurnType.DATA_QUERY_REQUEST) is True
    assert should_inject_ltm(TurnType.SKILL_EXECUTION) is True
    assert should_inject_ltm(TurnType.GENERAL) is True


def test_classify_turn_from_intent_data_query_request_via_reasoning():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.92,
        reasoning="用户是对上一轮表格结果的分析追问",
        entities=[],
    )
    # 通用分类只保留“数据查询请求”，不表达 ChatBI 具体请求类别。
    cached_result_turn = classify_turn_from_intent(intent, can_do_data=True, user_query="分析一下", has_last_data_result=True)
    assert cached_result_turn.turn_type == TurnType.DATA_QUERY_REQUEST

    no_cached_result_turn = classify_turn_from_intent(intent, can_do_data=True, user_query="分析一下", has_last_data_result=False)
    assert no_cached_result_turn.turn_type == TurnType.DATA_QUERY_REQUEST


def test_classify_turn_from_intent_data_query_request_via_query_keywords():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.95,
        reasoning="数据图表请求",
        entities=[],
    )
    # 情况 C：有缓存，提问中含有“折线图”等广义追问词 → 仍只表达数据查询请求
    followup_turn = classify_turn_from_intent(intent, can_do_data=True, user_query="帮我转为折线图", has_last_data_result=True)
    assert followup_turn.turn_type == TurnType.DATA_QUERY_REQUEST

    # 情况 D：有缓存，但原句纯粹是查数且推理无追问 → 仍只表达数据查询请求
    new_query_turn = classify_turn_from_intent(intent, can_do_data=True, user_query="查询上海数据", has_last_data_result=True)
    assert new_query_turn.turn_type == TurnType.DATA_QUERY_REQUEST


def test_classify_turn_heuristic_formatting_correction_to_data_query_request():
    # 验证在快通道中，排版/渲染纠错命令且有缓存数据时，能直接短路识别为数据查询请求
    query = "你输出的 markdown 不符合规范，渲染失败呢"
    
    # 通用分类只表达数据查询请求；复用上一轮结果由 DataQueryTurnClassifier 内部处理。
    cached_result_turn = classify_turn_heuristic(query, can_do_data=True, has_last_data_result=True)
    assert cached_result_turn is not None
    assert cached_result_turn.turn_type == TurnType.DATA_QUERY_REQUEST
    assert cached_result_turn.skip_intent_llm is True

    # 情况 B：无缓存 → 规则不短路，返回 None 交给大模型进行深层语义兜底/分析
    res_none = classify_turn_heuristic(query, can_do_data=True, has_last_data_result=False)
    assert res_none is None


@pytest.mark.asyncio
async def test_data_query_turn_classifier_owns_reuse_previous_result_semantics():
    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(side_effect=AssertionError("reuse follow-up should short-circuit before classifier LLM")),
    ):
        classification, _, elapsed_ms = await resolve_data_query_turn_classification(
            "可视化分析一下",
            [
                {"role": "user", "content": "查询算力SU回款"},
                {"role": "assistant", "content": "| 月份 | 回款率 |\n| --- | --- |\n| 2026-01 | 85.5% |"},
                {"role": "user", "content": "可视化分析一下"},
            ],
            has_last_data_result=True,
        )

    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False
    assert classification.requires_few_shot is False
    assert classification.skip_intent_llm is True
    assert elapsed_ms == 0.0


@pytest.mark.asyncio
async def test_data_query_turn_classifier_reuses_from_history_when_redis_missing():
    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(side_effect=AssertionError("history reuse should short-circuit before classifier LLM")),
    ):
        classification, _, elapsed_ms = await resolve_data_query_turn_classification(
            "可视化分析一下",
            [
                {"role": "user", "content": "查询算力SU回款"},
                {
                    "role": "assistant",
                    "content": (
                        "| 月份 | 总应收款(万元) | 回款率 |\n"
                        "| --- | --- | --- |\n"
                        "| 2026-01 | 1027.32 | 85.55% |\n"
                        "### 分析解读\n回款率在 3 月超过 100%。"
                    ),
                },
                {"role": "user", "content": "可视化分析一下"},
            ],
            has_last_data_result=False,
        )

    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False
    assert classification.skip_intent_llm is True
    assert "查数展示" in classification.reasoning or "结构化查询结果" in classification.reasoning
    assert elapsed_ms == 0.0


@pytest.mark.asyncio
async def test_data_query_turn_classifier_reuses_result_for_date_formatting_followup():
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"用户只要求调整上一轮结果的日期展示格式"}')

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=llm)), \
         patch("app.services.ai.data_query_turn_classifier.chat_client_from_handle", return_value=chat_client):
        classification, _, _ = await resolve_data_query_turn_classification(
            "创建日期按 yyyy-MM-dd 显示",
            [{"role": "user", "content": "创建日期按 yyyy-MM-dd 显示"}],
            has_last_data_result=True,
        )

    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False
    assert classification.requires_few_shot is False


@pytest.mark.asyncio
async def test_data_query_turn_classifier_metadata_query_uses_schema_without_sql():
    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(side_effect=AssertionError("metadata query should short-circuit before classifier LLM")),
    ):
        classification, intent_info, elapsed_ms = await resolve_data_query_turn_classification(
            "说明智能体有哪些可查询字段和分析口径",
            [{"role": "user", "content": "说明智能体有哪些可查询字段和分析口径"}],
            has_last_data_result=False,
        )

    assert classification.turn_type == DataQueryTurnType.METADATA_QUERY
    assert classification.requires_fresh_data is True
    assert classification.requires_sql_query is False
    assert classification.requires_few_shot is False
    assert classification.skip_intent_llm is True
    assert intent_info.intent == IntentType.DATA_QUERY
    assert elapsed_ms == 0.0


@pytest.mark.asyncio
async def test_data_query_turn_classifier_rejects_reuse_without_reusable_result():
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"用户是在要求可视化上一轮结果"}')

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ) as mock_get_llm, patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, _, _ = await resolve_data_query_turn_classification(
            "可视化分析一下",
            [{"role": "user", "content": "可视化分析一下"}],
            has_last_data_result=False,
        )

    mock_get_llm.assert_awaited_once()
    assert classification.turn_type == DataQueryTurnType.CLARIFICATION_OR_NON_DATA
    assert classification.requires_fresh_data is False
    assert classification.requires_few_shot is False
    assert "可信" in classification.reasoning or "没有可复用" in classification.reasoning


@pytest.mark.asyncio
async def test_data_query_turn_classifier_short_circuits_general_chat_without_sql():
    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(side_effect=AssertionError("general chat should not call classifier LLM")),
    ):
        classification, intent_info, elapsed_ms = await resolve_data_query_turn_classification(
            "你好，你是谁",
            [],
            has_last_data_result=False,
        )

    assert classification.turn_type == DataQueryTurnType.CLARIFICATION_OR_NON_DATA
    assert classification.requires_fresh_data is False
    assert classification.requires_few_shot is False
    assert classification.skip_intent_llm is True
    assert intent_info.intent == IntentType.GENERAL
    assert elapsed_ms == 0.0


@pytest.mark.asyncio
async def test_data_query_turn_classifier_requires_recent_context_for_reuse():
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"用户要求分析上一轮结果"}')

    stale_history = [
        {"role": "user", "content": "查询用户列表"},
        {"role": "assistant", "content": "已返回用户列表。"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好。"},
        {"role": "user", "content": "你是谁"},
        {"role": "assistant", "content": "我是助手。"},
        {"role": "user", "content": "帮我写个说明"},
        {"role": "assistant", "content": "好的。"},
        {"role": "user", "content": "分析一下"},
    ]

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ), patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, _, _ = await resolve_data_query_turn_classification(
            "分析一下",
            stale_history,
            has_last_data_result=True,
        )

    assert classification.turn_type == DataQueryTurnType.CLARIFICATION_OR_NON_DATA
    assert classification.requires_fresh_data is False
    assert "最近对话" in classification.reasoning


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "我要看各机房数据采集是否延迟",
        "我要看各门店库存是否异常",
        "检查各客户订单同步状态",
        "查看各项目回款是否延迟",
        "确认各渠道线索是否断流",
    ],
)
async def test_data_query_turn_classifier_rescues_business_status_query_from_stale_reuse(query):
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"误判为基于上一轮结果继续分析"}')

    stale_history = [
        {"role": "user", "content": "查询用户列表"},
        {"role": "assistant", "content": "已返回用户列表。"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好。"},
        {"role": "user", "content": "你是谁"},
        {"role": "assistant", "content": "我是助手。"},
        {"role": "user", "content": "帮我写个说明"},
        {"role": "assistant", "content": "好的。"},
        {"role": "user", "content": query},
    ]

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ), patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, _, _ = await resolve_data_query_turn_classification(
            query,
            stale_history,
            has_last_data_result=True,
        )

    assert classification.turn_type == DataQueryTurnType.NEW_DATA_QUERY
    assert classification.requires_fresh_data is True
    assert classification.requires_few_shot is True
    assert "明确新数据查询" in classification.reasoning


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["帮我看看这个状态", "这个是否正常"])
async def test_data_query_turn_classifier_keeps_vague_status_followup_as_clarification(query):
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"用户像是在追问上一轮状态"}')

    stale_history = [
        {"role": "user", "content": "查询用户列表"},
        {"role": "assistant", "content": "已返回用户列表。"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好。"},
        {"role": "user", "content": "你是谁"},
        {"role": "assistant", "content": "我是助手。"},
        {"role": "user", "content": "帮我写个说明"},
        {"role": "assistant", "content": "好的。"},
        {"role": "user", "content": query},
    ]

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ), patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, _, _ = await resolve_data_query_turn_classification(
            query,
            stale_history,
            has_last_data_result=True,
        )

    assert classification.turn_type == DataQueryTurnType.CLARIFICATION_OR_NON_DATA
    assert "最近对话" in classification.reasoning


@pytest.mark.asyncio
async def test_data_query_turn_classifier_allows_polite_followup_with_reusable_result():
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"用户礼貌地要求分析当前数据结果"}')

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ) as mock_get_llm, patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, _, _ = await resolve_data_query_turn_classification(
            "你好，帮我分析这个数据",
            [
                {"role": "assistant", "content": "已返回用户列表数据结果。"},
                {"role": "user", "content": "你好，帮我分析这个数据"},
            ],
            has_last_data_result=True,
        )

    mock_get_llm.assert_awaited_once()
    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False


@pytest.mark.asyncio
async def test_data_query_turn_classifier_uses_llm_for_explicit_new_query():
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"new_data_query","reasoning":"用户明确询问用户数量，需要重新查询数据"}')

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ) as mock_get_llm, patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, _, _ = await resolve_data_query_turn_classification(
            "How many users?",
            [{"role": "user", "content": "How many users?"}],
            has_last_data_result=False,
        )

    mock_get_llm.assert_awaited_once()
    assert classification.turn_type == DataQueryTurnType.NEW_DATA_QUERY
    assert classification.requires_fresh_data is True
    assert classification.skip_intent_llm is False


@pytest.mark.asyncio
async def test_data_query_turn_classifier_falls_back_to_rules_when_llm_invalid():
    llm = object()
    chat_client = _mock_chat_client("not json")

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=llm)), \
         patch("app.services.ai.data_query_turn_classifier.chat_client_from_handle", return_value=chat_client):
        classification, _, _ = await resolve_data_query_turn_classification(
            "查询用户列表并可视化分析",
            [{"role": "user", "content": "查询用户列表并可视化分析"}],
            has_last_data_result=False,
        )

    assert classification.turn_type == DataQueryTurnType.NEW_DATA_QUERY
    assert classification.requires_fresh_data is True
    assert classification.skip_intent_llm is True


@pytest.mark.asyncio
async def test_data_query_turn_classifier_uses_llm_fallback_with_history_context():
    content = (
        '{"turn_type":"reuse_previous_result",'
        '"reasoning":"用户是在要求把上一轮用户列表里的日期列改成短日期展示，不需要重新查数"}'
    )
    llm = object()
    chat_client = _mock_chat_client(content)

    with patch(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=llm),
    ) as mock_get_llm, patch(
        "app.services.ai.data_query_turn_classifier.chat_client_from_handle",
        return_value=chat_client,
    ):
        classification, intent_info, elapsed_ms = await resolve_data_query_turn_classification(
            "把那列弄成短日期就行",
            [
                {"role": "user", "content": "帮我查一下用户列表的数据呢"},
                {"role": "assistant", "content": "已返回用户列表，其中包含创建日期字段。"},
                {"role": "user", "content": "把那列弄成短日期就行"},
            ],
            has_last_data_result=True,
        )

    mock_get_llm.assert_awaited_once()
    chat_client.generate_text.assert_awaited_once()
    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False
    assert classification.requires_few_shot is False
    assert classification.skip_intent_llm is False
    assert "短日期" in classification.reasoning
    assert intent_info.confidence == 0.85
    assert elapsed_ms >= 0
