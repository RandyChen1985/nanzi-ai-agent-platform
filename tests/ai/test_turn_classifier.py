import pytest

from app.services.ai.turn_classifier import (
    TurnClassification,
    TurnType,
    adapt_classification_for_agent,
    classify_turn_heuristic,
    classify_turn_from_intent,
    turn_type_label,
    should_inject_user_context,
)
from app.services.ai.intent_service import IntentResponse, IntentType


@pytest.mark.parametrize(
    "query,expected",
    [
        ("把这个流程保存为技能", TurnType.META_ACTION),
        ("保存这个结果", TurnType.K3_CONTEXT_ACTION),
        ("使用用户列表查询技能", TurnType.SKILL_EXECUTION),
        ("查询用户列表并可视化分析", TurnType.K1_NEW_QUERY),
        ("高温告警的标准处理流程是什么", TurnType.KNOWLEDGE),
    ],
)
def test_classify_turn_heuristic_fixed_cases(query, expected):
    result = classify_turn_heuristic(query, can_do_data=True, has_last_data_result=False)
    assert result is not None
    assert result.turn_type == expected


def test_k2_requires_cached_result():
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
    assert result.turn_type == TurnType.K2_REUSE_RESULT
    assert result.skip_intent_llm is True
    assert result.requires_few_shot is False


def test_classify_turn_from_intent_data_query():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.9,
        reasoning="查业务数据",
        entities=[],
    )
    result = classify_turn_from_intent(intent, can_do_data=True)
    assert result.turn_type == TurnType.K1_NEW_QUERY
    assert result.use_data_executor is True


def test_classify_turn_from_intent_knowledge():
    intent = IntentResponse(
        intent=IntentType.KNOWLEDGE_BASE,
        confidence=0.88,
        reasoning="问 SOP",
        entities=[],
    )
    result = classify_turn_from_intent(intent, can_do_data=True)
    assert result.turn_type == TurnType.KNOWLEDGE
    assert result.use_data_executor is False
    assert result.requires_knowledge_search is True


def test_turn_type_label():
    assert turn_type_label(TurnType.K2_REUSE_RESULT) == "K2 复用结果"


def test_adapt_classification_for_non_data_agent():
    base = classify_turn_heuristic("查询用户列表", can_do_data=True, has_last_data_result=False)
    assert base is None or base.turn_type == TurnType.K1_NEW_QUERY
    k1 = TurnClassification(
        turn_type=TurnType.K1_NEW_QUERY,
        reasoning="test",
        use_data_executor=True,
        intent=IntentType.DATA_QUERY,
    )
    adapted = adapt_classification_for_agent(k1, can_do_data=False)
    assert adapted.use_data_executor is False
    assert adapted.turn_type == TurnType.K1_NEW_QUERY


def test_should_inject_user_context():
    assert should_inject_user_context(TurnType.K1_NEW_QUERY) is False
    assert should_inject_user_context(TurnType.K3_CONTEXT_ACTION) is True


def test_classify_turn_from_intent_k2_upgrade_via_reasoning():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.92,
        reasoning="用户是对上一轮表格结果的分析追问",
        entities=[],
    )
    # 情况 A：有缓存，且推理中含有追问特征 → 升级为 K2
    res_k2 = classify_turn_from_intent(intent, can_do_data=True, user_query="分析一下", has_last_data_result=True)
    assert res_k2.turn_type == TurnType.K2_REUSE_RESULT
    assert res_k2.requires_fresh_data is False

    # 情况 B：无缓存，即便符合追问语义 → 退水回 K1，防止无本之木
    res_k1 = classify_turn_from_intent(intent, can_do_data=True, user_query="分析一下", has_last_data_result=False)
    assert res_k1.turn_type == TurnType.K1_NEW_QUERY
    assert res_k1.requires_fresh_data is True


def test_classify_turn_from_intent_k2_upgrade_via_query_keywords():
    intent = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.95,
        reasoning="数据图表请求",
        entities=[],
    )
    # 情况 C：有缓存，提问中含有“折线图”等广义追问词 → 升级为 K2
    res_k2 = classify_turn_from_intent(intent, can_do_data=True, user_query="帮我转为折线图", has_last_data_result=True)
    assert res_k2.turn_type == TurnType.K2_REUSE_RESULT
    assert res_k2.requires_fresh_data is False

    # 情况 D：有缓存，但原句纯粹是查数且推理无追问 → 维持 K1 新查数
    res_k1 = classify_turn_from_intent(intent, can_do_data=True, user_query="查询上海数据", has_last_data_result=True)
    assert res_k1.turn_type == TurnType.K1_NEW_QUERY
    assert res_k1.requires_fresh_data is True

