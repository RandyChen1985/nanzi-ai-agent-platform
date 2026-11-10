"""Tests for request_user_confirmation business data confirmation tool."""
from __future__ import annotations

import json

import pytest

from app.services.ai.business_confirmation import (
    BUSINESS_CONFIRMATION_TOOL_NAME,
    build_business_confirmation_sse,
    parse_confirmation_tool_output,
)
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.tools.user_confirmation_tools import request_user_confirmation


pytestmark = pytest.mark.no_infrastructure


def test_request_user_confirmation_is_implicit_read_only():
    tool_names = {getattr(tool, "name", "") for tool in ToolRegistry.get_system_implicit_tools()}
    assert BUSINESS_CONFIRMATION_TOOL_NAME in tool_names
    assert ToolRegistry._registry[BUSINESS_CONFIRMATION_TOOL_NAME] is request_user_confirmation

    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(request_user_confirmation, source_type="system")
    assert spec.permission_scope == "read"
    assert spec.is_read_only is True


@pytest.mark.asyncio
async def test_request_user_confirmation_returns_awaiting_user_ui_payload():
    result = await request_user_confirmation.ainvoke(
        {
            "title": "请确认供应商信息",
            "summary": "确认后录入 ITAM",
            "fields": [
                {
                    "key": "supplier_name",
                    "label": "供应商名称",
                    "value": "北京神马科技有限公司",
                    "editable": True,
                    "value_type": "string",
                }
            ],
            "confirm_label": "确定",
            "cancel_label": "取消",
            "risk_note": "请核对原文",
        }
    )
    payload = json.loads(result)
    assert payload["status"] == "awaiting_user"
    assert payload["confirmation_id"].startswith("bc_")
    assert payload["ui"]["title"] == "请确认供应商信息"
    assert payload["ui"]["fields"][0]["key"] == "supplier_name"
    assert payload["ui"]["fields"][0]["value"] == "北京神马科技有限公司"
    assert payload["ui"]["confirm_label"] == "确定"
    assert payload["ui"]["risk_note"] == "请核对原文"


@pytest.mark.asyncio
async def test_request_user_confirmation_rejects_empty_fields():
    result = await request_user_confirmation.ainvoke({"title": "x", "fields": []})
    assert "fields" in result.lower() or "字段" in result


def test_build_business_confirmation_sse_from_tool_output():
    raw = json.dumps(
        {
            "status": "awaiting_user",
            "confirmation_id": "bc_test",
            "message": "等待用户确认",
            "ui": {
                "title": "确认",
                "summary": "摘要",
                "fields": [{"key": "a", "label": "A", "value": "1", "editable": True, "value_type": "string"}],
                "confirm_label": "确定",
                "cancel_label": "取消",
                "risk_note": "",
            },
        },
        ensure_ascii=False,
    )
    assert parse_confirmation_tool_output(raw)["confirmation_id"] == "bc_test"
    event = build_business_confirmation_sse(
        tool_name=BUSINESS_CONFIRMATION_TOOL_NAME,
        tool_output=raw,
        tool_call_id="call_1",
    )
    assert event is not None
    assert event["type"] == "business_confirmation"
    assert event["confirmation_id"] == "bc_test"
    assert event["tool_call_id"] == "call_1"
    assert event["title"] == "确认"
    assert event["fields"][0]["key"] == "a"


def test_build_business_confirmation_sse_ignores_other_tools():
    assert (
        build_business_confirmation_sse(
            tool_name="get_myinfo",
            tool_output="{}",
            tool_call_id="x",
        )
        is None
    )


def test_build_user_confirmation_message_format():
    from app.services.ai.business_confirmation import build_user_confirmation_message

    text = build_user_confirmation_message(
        confirmed=True,
        confirmation_id="bc_abc",
        fields=[{"key": "name", "label": "名称", "value": "测试"}],
    )
    assert "【业务确认】用户已确定" in text
    assert "confirmation_id: bc_abc" in text
    assert "名称 (name): 测试" in text


def test_cancel_message_forbids_reopening_confirmation_card():
    from app.services.ai.business_confirmation import build_user_confirmation_message
    from app.services.ai.agent_prompts import AgentServicePrompts

    text = build_user_confirmation_message(
        confirmed=False,
        confirmation_id="bc_cancel",
        fields=[{"key": "name", "label": "名称", "value": "测试"}],
    )
    assert "禁止再次调用 request_user_confirmation" in text
    assert "不要重新弹确认卡" in text
    section = AgentServicePrompts._PLATFORM_BUSINESS_CONFIRMATION_SECTION
    assert "禁止再次调用 request_user_confirmation" in section
    assert "不得重新弹确认卡" in section
    assert "禁止再输出任何 `quick:` 链接" in section or "禁止再输出任何 quick" in section
    assert "确认/取消只走确认卡按钮" in section


@pytest.mark.asyncio
async def test_cancel_gate_hard_blocks_tool_and_sse():
    from app.services.ai.business_confirmation import (
        arm_cancel_confirmation_gate,
        build_business_confirmation_sse,
    )

    arm_cancel_confirmation_gate("【业务确认】用户已取消\nconfirmation_id: bc_x")
    blocked = await request_user_confirmation.ainvoke(
        {
            "title": "不应出卡",
            "fields": [{"key": "a", "label": "A", "value": "1"}],
        }
    )
    payload = json.loads(blocked)
    assert payload["status"] == "error"
    assert payload["error"] == "business_confirmation_cancelled"
    assert (
        build_business_confirmation_sse(
            tool_name=BUSINESS_CONFIRMATION_TOOL_NAME,
            tool_output=json.dumps(
                {
                    "status": "awaiting_user",
                    "confirmation_id": "bc_should_not",
                    "ui": {
                        "title": "x",
                        "fields": [{"key": "a", "label": "A", "value": "1"}],
                    },
                }
            ),
            tool_call_id="c1",
        )
        is None
    )
    # Clear gate for other tests
    arm_cancel_confirmation_gate("普通用户消息")


@pytest.mark.asyncio
async def test_request_user_confirmation_refuses_in_delegated_context():
    """委派/定时任务里没有可交互用户：必须明确拒绝，而不是发一张能等却等不到回执的死卡。

    业务确认卡（``business_confirmation``）没有被委派流白名单转发，子代理若以为
    「已展示」就会原地等一个永远不会到的【业务确认】回执，最后交出一段莫名其妙的结论。
    """
    from app.core.context import execution_options_context

    token = execution_options_context.set({"delegated_sub_agent": True})
    try:
        result = json.loads(
            await request_user_confirmation.ainvoke(
                {
                    "title": "请确认供应商信息",
                    "fields": [{"key": "supplier_name", "label": "供应商名称", "value": "A 公司"}],
                }
            )
        )
    finally:
        execution_options_context.reset(token)

    assert result["status"] == "error"
    assert "无人值守" in result["error"]
    assert "结论" in result["hint"]
