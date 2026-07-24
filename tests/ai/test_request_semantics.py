import pytest
from types import SimpleNamespace

from app.services.ai.intent_service import IntentResponse, IntentType, IntentService
from app.services.ai.request_decision import (
    RequestCapability,
    RequestSource,
    resolve_request_decision,
)
from app.services.ai.runners.chatbi.system_prompt import build_data_query_state_hint


pytestmark = pytest.mark.no_infrastructure


def test_intent_response_carries_reference_and_freshness_decision():
    response = IntentResponse(
        intent=IntentType.DATA_QUERY,
        confidence=0.96,
        reasoning="查询本周业务指标",
        domain="chatbi_business_data",
        operation="aggregate",
        fact_kind="business_metric",
        freshness_requirement="dynamic",
        time_scope="本周",
        reference_mode="new_query",
        needs_fresh_data=True,
    )

    assert response.reference_mode == "new_query"
    assert response.needs_fresh_data is True


def test_intent_format_instructions_describe_canonical_semantic_frame():
    instructions = IntentService._format_instructions()

    assert "reference_mode" in instructions
    assert "needs_fresh_data" in instructions


def test_local_file_aggregate_does_not_open_chatbi_route_even_with_data_intent():
    decision = resolve_request_decision(
        "统计一下我机器的文件数",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.96,
        semantic_domain="local_file",
        semantic_operation="aggregate",
        fact_kind="file_count",
        freshness_requirement="dynamic",
        reference_mode="new_query",
        needs_fresh_data=True,
    )

    assert decision.source == RequestSource.GENERAL
    assert decision.capability == RequestCapability.ANSWER
    assert decision.allows_data_route is False
    assert decision.should_delegate is False
    assert decision.needs_fresh_data is True


def test_chatbi_business_metric_is_a_fresh_new_query():
    decision = resolve_request_decision(
        "查询本周各区域销售额并可视化",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.96,
        semantic_domain="chatbi_business_data",
        semantic_operation="visualize",
        fact_kind="business_metric",
        freshness_requirement="dynamic",
        time_scope="本周",
        reference_mode="new_query",
        needs_fresh_data=True,
    )

    assert decision.source == RequestSource.INTERNAL_STRUCTURED_DATA
    assert decision.capability == RequestCapability.DATA_QUERY
    assert decision.reference_mode == "new_query"
    assert decision.needs_fresh_data is True
    assert decision.time_scope == "本周"


def test_explicit_previous_result_reuse_does_not_require_fresh_data():
    decision = resolve_request_decision(
        "把刚才的结果画成柱状图",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
        semantic_domain="conversation_context",
        semantic_operation="visualize",
        fact_kind="previous_query_result",
        freshness_requirement="reuse_previous",
        reference_mode="reuse_previous",
        needs_fresh_data=False,
        has_last_data_result=True,
    )

    assert decision.capability == RequestCapability.CONTEXT_TRANSFORM
    assert decision.reference_mode == "reuse_previous"
    assert decision.needs_fresh_data is False


def test_contextual_business_followup_can_still_request_new_data():
    decision = resolve_request_decision(
        "基于刚才的客户范围，再查本周订单数",
        semantic_intent=IntentType.DATA_QUERY,
        semantic_confidence=0.94,
        semantic_domain="conversation_context",
        semantic_operation="aggregate",
        fact_kind="business_metric",
        freshness_requirement="dynamic",
        reference_mode="data_followup_query",
        needs_fresh_data=True,
        has_last_data_result=True,
    )

    assert decision.capability == RequestCapability.DATA_QUERY
    assert decision.reference_mode == "data_followup_query"
    assert decision.needs_fresh_data is True


def test_chatbi_state_hint_exposes_semantics_and_evidence_metadata():
    hint = build_data_query_state_hint(
        SimpleNamespace(
            _requires_fresh_data=True,
            _requires_sql_query=True,
            route_hints={
                "semantic_domain": "chatbi_business_data",
                "semantic_operation": "aggregate",
                "fact_kind": "business_metric",
                "freshness_requirement": "dynamic",
                "time_scope": "本周",
                "reference_mode": "new_query",
                "needs_fresh_data": True,
            },
            _evidence_metadata={
                "status": "success_non_empty",
                "source_ref": "dataset://orders",
                "observed_at": "2026-07-24T10:00:00+00:00",
                "source_as_of": "2026-07-24T09:59:00+00:00",
                "freshness": "dynamic",
            },
        )
    )

    assert "semantic_domain: chatbi_business_data" in hint
    assert "reference_mode: new_query" in hint
    assert "result_status: success_non_empty" in hint
    assert "source_ref: dataset://orders" in hint
    assert "source_as_of: 2026-07-24T09:59:00+00:00" in hint
