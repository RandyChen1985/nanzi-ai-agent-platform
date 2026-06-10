import sys
import types

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_runtime_message_preserves_text_image_and_tool_result_blocks():
    from app.services.ai.runtime.agentscope.messages import (
        RuntimeContentBlock,
        RuntimeMessage,
        convert_history_to_runtime_messages,
    )

    history = [
        {"role": "system", "content": "system prompt"},
        {
            "role": "user",
            "content": "看一下图片",
            "attachments": [
                {
                    "mime_type": "image/png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
        {"role": "assistant", "content": "准备调用工具"},
        {
            "role": "tool",
            "content": "{\"rows\": []}",
            "tool_call_id": "call_1",
            "name": "execute_sql_query",
        },
    ]

    messages = convert_history_to_runtime_messages(history)

    assert messages == [
        RuntimeMessage(role="system", content=[RuntimeContentBlock(type="text", text="system prompt")]),
        RuntimeMessage(
            role="user",
            content=[
                RuntimeContentBlock(type="text", text="看一下图片"),
                RuntimeContentBlock(
                    type="image",
                    mime_type="image/png",
                    data="data:image/png;base64,abc",
                ),
            ],
        ),
        RuntimeMessage(role="assistant", content=[RuntimeContentBlock(type="text", text="准备调用工具")]),
        RuntimeMessage(
            role="tool",
            content=[RuntimeContentBlock(type="text", text="{\"rows\": []}")],
            tool_call_id="call_1",
            tool_name="execute_sql_query",
        ),
    ]


def test_agentscope_event_adapter_keeps_existing_sse_contract():
    from app.services.ai.runtime.agentscope.events import AgentScopeEventAdapter

    adapter = AgentScopeEventAdapter(trace_id="trace_1")

    assert adapter.to_sse_chunk(types.SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="你好")) == {
        "content": "你好"
    }
    assert adapter.to_sse_chunk(types.SimpleNamespace(type="THINKING_BLOCK_START")) == {
        "type": "thinking",
        "status": "continuing",
    }
    assert adapter.to_sse_chunk(
        types.SimpleNamespace(
            type="TOOL_CALL_START",
            id="evt_1",
            name="execute_sql_query",
            arguments={"sql": "SELECT 1"},
        )
    ) == {
        "type": "log",
        "id": "evt_1",
        "title": "调用工具: execute_sql_query",
        "details": "{\"sql\": \"SELECT 1\"}",
        "status": "pending",
        "category": "tool",
    }
    assert adapter.to_sse_chunk(types.SimpleNamespace(type="CUSTOM", payload={"x": 1})) is None


def test_workspace_key_sanitizes_trace_and_conversation_ids():
    from app.services.ai.runtime.agentscope.workspace import build_workspace_key

    assert (
        build_workspace_key(trace_id="trace/../../abc", conversation_id="conv:001")
        == "trace_abc__conv_001"
    )
    assert build_workspace_key(trace_id="", conversation_id=None).startswith("trace_")


def test_verify_agentscope_imports_reports_missing_modules(monkeypatch):
    from app.services.ai.runtime.agentscope.imports import verify_agentscope_imports

    monkeypatch.setitem(sys.modules, "agentscope.agent", types.ModuleType("agentscope.agent"))
    monkeypatch.setitem(sys.modules, "agentscope.model", types.ModuleType("agentscope.model"))

    result = verify_agentscope_imports(required_modules=["agentscope.agent", "agentscope.model", "missing.module"])

    assert result.ok is False
    assert result.available_modules == ["agentscope.agent", "agentscope.model"]
    assert result.missing_modules == ["missing.module"]


def test_runtime_error_envelope_normalizes_unknown_errors():
    from app.services.ai.runtime.agentscope.errors import error_to_envelope

    envelope = error_to_envelope(ValueError("bad value"))

    assert envelope.kind == "unknown"
    assert envelope.message == "bad value"
    assert envelope.details == {}


@pytest.mark.asyncio
async def test_model_factory_requires_api_key_for_openai_compatible_model(monkeypatch):
    from app.services.ai.runtime.agentscope.models import AgentScopeModelConfig, create_openai_chat_model

    with pytest.raises(ValueError, match="LLM API Key is missing"):
        create_openai_chat_model(
            AgentScopeModelConfig(
                api_key="",
                base_url="https://llm.example.com/v1",
                model="deepseek-chat",
                temperature=0.1,
                streaming=True,
            )
        )
