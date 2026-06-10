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
    assert should_inject_user_context(TurnType.DATA_QUERY_REQUEST) is False
    assert should_inject_user_context(TurnType.CONTEXT_ACTION) is True


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
    llm = object()
    chat_client = _mock_chat_client('{"turn_type":"reuse_previous_result","reasoning":"用户要求基于上一轮结果做可视化"}')

    with patch("app.services.ai.config.AgentConfigProvider.get_configured_llm", AsyncMock(return_value=llm)), \
         patch("app.services.ai.data_query_turn_classifier.chat_client_from_handle", return_value=chat_client):
        classification, _, _ = await resolve_data_query_turn_classification(
            "可视化分析一下",
            [{"role": "user", "content": "可视化分析一下"}],
            has_last_data_result=True,
        )

    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False
    assert classification.requires_few_shot is False


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
async def test_data_query_turn_classifier_marks_followup_even_without_reusable_result():
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
    assert classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT
    assert classification.requires_fresh_data is False
    assert "可视化" in classification.reasoning


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
