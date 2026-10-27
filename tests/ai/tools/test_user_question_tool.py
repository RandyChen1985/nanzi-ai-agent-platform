"""Tests for the AI-initiated user question interaction."""
from __future__ import annotations

import json

import pytest

from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools.user_question_tools import ask_user_question
from app.services.ai.user_question import (
    USER_QUESTION_TOOL_NAME,
    build_user_question_sse,
    build_user_question_receipt,
    metadata_dataset_ids_from_user_question_record,
    parse_user_question_receipt,
    parse_user_question_tool_output,
)


pytestmark = pytest.mark.no_infrastructure


def _question_args() -> dict[str, object]:
    return {
        "question": "希望按哪个时间维度统计销售额？",
        "options": [
            {"id": "daily", "label": "按天", "description": "查看每日趋势"},
            {"id": "monthly", "label": "按月", "description": "查看月度汇总"},
        ],
        "is_multi_select": False,
        "allow_custom_input": True,
        "context": "当前数据集包含过去一年的交易明细",
    }


def test_ask_user_question_is_global_interaction_tool():
    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}
    assert USER_QUESTION_TOOL_NAME in tool_names
    assert ToolRegistry._registry[USER_QUESTION_TOOL_NAME] is ask_user_question

    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(ask_user_question, source_type="system")
    assert spec.permission_scope == "read"
    assert spec.is_read_only is True


@pytest.mark.asyncio
async def test_ask_user_question_returns_structured_awaiting_user_payload():
    result = await ask_user_question.ainvoke(_question_args())
    payload = json.loads(result)

    assert payload["status"] == "awaiting_user"
    assert payload["interaction_type"] == "question"
    assert payload["question_id"].startswith("uq_")
    assert payload["question"] == "希望按哪个时间维度统计销售额？"
    assert [option["id"] for option in payload["options"]] == ["daily", "monthly"]
    assert payload["is_multi_select"] is False
    assert payload["allow_custom_input"] is True


@pytest.mark.asyncio
async def test_ask_user_question_preserves_controlled_purpose():
    arguments = _question_args()
    arguments["purpose"] = "chatbi_dataset_selection"

    result = await ask_user_question.ainvoke(arguments)
    payload = json.loads(result)

    assert payload["purpose"] == "chatbi_dataset_selection"


@pytest.mark.asyncio
async def test_ask_user_question_rejects_invalid_options():
    result = await ask_user_question.ainvoke(
        {
            "question": "选择一个维度",
            "options": [{"id": "only", "label": "唯一选项"}],
        }
    )
    assert "至少" in result or "options" in result

    duplicate = await ask_user_question.ainvoke(
        {
            "question": "选择一个维度",
            "options": [
                {"id": "same", "label": "选项一"},
                {"id": "same", "label": "选项二"},
            ],
        }
    )
    assert "唯一" in duplicate or "重复" in duplicate


def test_question_payload_builds_sse_event_and_validated_receipt():
    raw = json.dumps(
        {
            "status": "awaiting_user",
            "interaction_type": "question",
            "question_id": "uq_test",
            "question": "希望按哪个时间维度统计销售额？",
            "options": _question_args()["options"],
            "is_multi_select": False,
            "allow_custom_input": True,
            "context": "当前数据集包含过去一年的交易明细",
            "purpose": "chatbi_dataset_selection",
        },
        ensure_ascii=False,
    )

    payload = parse_user_question_tool_output(raw)
    assert payload is not None
    assert payload["question_id"] == "uq_test"

    event = build_user_question_sse(
        tool_name=USER_QUESTION_TOOL_NAME,
        tool_output=raw,
        tool_call_id="call_1",
    )
    assert event is not None
    assert event["type"] == "user_question"
    assert event["question_id"] == "uq_test"
    assert event["tool_call_id"] == "call_1"
    assert event["purpose"] == "chatbi_dataset_selection"

    receipt = build_user_question_receipt(
        question_id="uq_test",
        selected_option_ids=["monthly"],
        custom_input="排除退款订单",
    )
    assert receipt.startswith("【用户回答】")
    assert "question_id: uq_test" in receipt
    assert 'selected_option_ids: ["monthly"]' in receipt
    assert "排除退款订单" in receipt

    cancelled_receipt = build_user_question_receipt(
        question_id="uq_test",
        selected_option_ids=[],
        cancelled=True,
    )
    assert "cancelled: true" in cancelled_receipt
    assert parse_user_question_receipt(cancelled_receipt) == {
        "question_id": "uq_test",
        "selected_option_ids": [],
        "custom_input": "",
        "cancelled": True,
    }


def test_question_sse_ignores_other_tools():
    assert (
        build_user_question_sse(
            tool_name="search_knowledge_base",
            tool_output="{}",
            tool_call_id="call_1",
        )
        is None
    )


def test_user_question_receipt_parser_and_prompt_guidance():
    from app.services.ai.agent_prompts import AgentServicePrompts
    from app.services.ai.user_question import parse_user_question_receipt

    receipt = (
        "【用户回答】\n"
        "interaction_type: question\n"
        "question_id: uq_test\n"
        'selected_option_ids: ["monthly"]\n'
        "custom_input: 排除退款订单"
    )
    parsed = parse_user_question_receipt(receipt)
    assert parsed == {
        "question_id": "uq_test",
        "selected_option_ids": ["monthly"],
        "custom_input": "排除退款订单",
        "cancelled": False,
    }
    assert "ask_user_question" in AgentServicePrompts._PLATFORM_USER_QUESTION_SECTION
    assert "必须停止" in AgentServicePrompts._PLATFORM_USER_QUESTION_SECTION
    assert "cancelled=true" in AgentServicePrompts._PLATFORM_USER_QUESTION_SECTION


def test_user_question_tool_and_prompt_support_explicit_interactive_requests():
    from app.services.ai.agent_prompts import AgentServicePrompts
    from app.services.ai.tools.user_question_tools import ask_user_question

    assert "明确要求互动式提问" in ask_user_question.description
    assert "用户明确要求提问" in AgentServicePrompts._PLATFORM_USER_QUESTION_SECTION
    assert "列出问题" in AgentServicePrompts._PLATFORM_USER_QUESTION_SECTION


def test_user_question_event_is_an_execution_interrupt():
    from app.services.ai.runtime.agentscope.event_stream import is_interrupt_sse_chunk

    assert is_interrupt_sse_chunk({"type": "user_question", "status": "pending"})


def test_dataset_question_record_can_restore_only_validated_numeric_ids():
    record = {
        "status": "submitted",
        "purpose": "chatbi_dataset_selection",
        "selected_option_ids": ["12"],
        "options": [{"id": "12", "label": "门禁数据"}],
    }
    assert metadata_dataset_ids_from_user_question_record(record) == ["12"]
    assert metadata_dataset_ids_from_user_question_record({**record, "purpose": "other"}) is None
    assert metadata_dataset_ids_from_user_question_record({**record, "selected_option_ids": ["ds-name"]}) is None
    assert metadata_dataset_ids_from_user_question_record({**record, "status": "cancelled"}) is None


def test_question_fallback_content_uses_latest_question_in_nested_timeline():
    """补全正文必须取最近一条提问卡，且能穿透子代理的嵌套步骤。"""
    from app.services.ai.user_question import build_user_question_fallback_content

    timeline = [
        {"category": "user_question", "user_question": {"question": "第一个问题"}},
        {
            "category": "tool",
            "children": [
                {"category": "user_question", "user_question": {"question": "子代理问题"}}
            ],
        },
        {"category": "user_question", "user_question": {"question": "最新问题"}},
    ]

    assert build_user_question_fallback_content(timeline) == "我需要你补充确认：最新问题"


def test_question_fallback_content_is_empty_without_question_card():
    """没有提问卡时不得伪造正文，避免把普通轮次写成「需要补充确认」。"""
    from app.services.ai.user_question import build_user_question_fallback_content

    assert build_user_question_fallback_content(None) == ""
    assert build_user_question_fallback_content([]) == ""
    assert build_user_question_fallback_content([{"category": "tool", "title": "Bash"}]) == ""


@pytest.fixture
def _delegated_options():
    """模拟委派链：子代理的 execution_options 只带委派标记。"""
    from app.core.context import execution_options_context

    token = execution_options_context.set({"delegated_sub_agent": True})
    yield
    execution_options_context.reset(token)


async def test_ask_user_question_refuses_in_delegated_context(_delegated_options):
    """委派链没有回传通道，发卡只会让子代理空等一个永远不会到的回执。"""
    result = json.loads(await ask_user_question.ainvoke(_question_args()))

    assert result["status"] == "error"
    assert "无人值守" in result["error"]
    assert "不要再尝试向用户提问" in result["hint"]


async def test_ask_user_question_still_works_for_interactive_turn():
    from app.core.context import execution_options_context

    token = execution_options_context.set({})
    try:
        result = json.loads(await ask_user_question.ainvoke(_question_args()))
    finally:
        execution_options_context.reset(token)

    assert result["status"] == "awaiting_user"
    assert result["question_id"].startswith("uq_")
