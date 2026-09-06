import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.agent_service import AgentService
from app.services.ai.runtime.agentscope.event_stream import (
    map_standard_agentscope_event,
    new_native_stream_state,
)


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_platform_context_summarized_event_is_persisted_with_scope_metadata():
    append_event = AsyncMock()
    service = AgentService()
    event = {
        "type": "context_summarized",
        "title": "对话上下文已压缩（平台摘录）",
        "dropped": 4,
        "kept": 8,
        "preview": "历史摘录",
        "token_used": 1200,
    }

    with patch(
        "app.services.ai.context.compactor.context_compaction_log_service.append_event",
        append_event,
    ):
        await service._persist_context_compaction_event(
            event,
            user_id="user-1",
            conversation_id="conversation-1",
            trace_id="trace-1",
            source="platform",
            stage="pre_route",
        )

    kwargs = append_event.await_args.kwargs
    assert kwargs["user_id"] == "user-1"
    assert kwargs["conversation_id"] == "conversation-1"
    assert kwargs["trace_id"] == "trace-1"
    assert kwargs["source"] == "platform"
    assert kwargs["stage"] == "pre_route"
    assert kwargs["event"]["type"] == "context_summarized"
    assert kwargs["event"]["dropped"] == 4
    assert kwargs["event"]["preview"] == "历史摘录"


@pytest.mark.asyncio
async def test_agentscope_context_compression_event_is_persisted_without_changing_sse():
    append_event = AsyncMock()
    state = new_native_stream_state()
    state["_observed_summary_len"] = 0

    class FakeRunner:
        trace_id = "trace-2"
        conversation_id = "conversation-2"

        def _runtime_user_id(self):
            return "user-2"

        def _runtime_agent_name(self):
            return "主助手"

    class FakeAgent:
        state = SimpleNamespace(summary="压缩后的上下文摘要")
        name = "主助手"

    event = SimpleNamespace(
        type="MODEL_CALL_END",
        reply_id="reply-1",
        input_tokens=12,
        output_tokens=4,
    )

    with patch(
        "app.services.ai.runtime.agentscope.event_stream.context_compaction_log_service.append_event",
        append_event,
    ):
        chunks = [
            chunk
            async for chunk in map_standard_agentscope_event(
                event,
                state=state,
                agent=FakeAgent(),
                runner=FakeRunner(),
                agent_name="主助手",
            )
        ]

    compression = next(chunk for chunk in chunks if chunk["type"] == "context_compression")
    assert compression["details"] == "压缩后的上下文摘要"
    kwargs = append_event.await_args.kwargs
    assert kwargs["user_id"] == "user-2"
    assert kwargs["conversation_id"] == "conversation-2"
    assert kwargs["trace_id"] == "trace-2"
    assert kwargs["source"] == "agentscope"
    assert kwargs["stage"] == "agent_runtime"
    assert kwargs["event"]["type"] == "context_compression"
    assert kwargs["event"]["summary_chars"] == len("压缩后的上下文摘要")


@pytest.mark.asyncio
async def test_platform_persistence_timeout_does_not_fail_the_sse_path():
    service = AgentService()

    async def _hang(*args, **kwargs):
        await asyncio.sleep(10)

    with patch(
        "app.services.ai.context.compactor.context_compaction_log_service.append_event",
        side_effect=_hang,
    ), patch(
        "app.services.ai.context.compactor.context_compaction_log_service.APPEND_TIMEOUT_SECONDS",
        0.01,
    ):
        await service._persist_context_compaction_event(
            {"type": "context_summarized"},
            user_id="user-1",
            conversation_id="conversation-1",
            trace_id="trace-1",
            source="platform",
            stage="pre_route",
        )


@pytest.mark.asyncio
async def test_resolved_model_compaction_persistence_uses_request_user_id():
    service = AgentService()
    agent_config = SimpleNamespace(
        agent_id="agent-1",
        agent_name="主助手",
        agent_display_name="主助手",
        capabilities=[],
        engine_config={},
        engine_type="LOCAL",
        model_name="target-model",
        system_prompt="",
        tools=[],
    )
    runtime_model_info = SimpleNamespace(
        configured_model="target-model",
        effective_model_id="target-model",
        source="agent_config",
        resolution_status="direct",
        public_dict=lambda: {},
    )
    shared_state = {}

    async def rebuild_context(*, shared_state, messages, **_kwargs):
        shared_state["context_final_compaction_event"] = {
            "type": "context_summarized",
            "preview": "早前对话",
        }
        return messages

    persist_event = AsyncMock()
    with patch.object(
        service,
        "_resolve_and_verify_agent",
        new=AsyncMock(return_value=(agent_config, None, 0.0, None)),
    ), patch.object(
        service,
        "_resolve_runtime_model_info_safe",
        new=AsyncMock(return_value=runtime_model_info),
    ), patch.object(
        service,
        "_rebuild_context_for_resolved_model",
        side_effect=rebuild_context,
    ), patch.object(
        service,
        "_runtime_context_metadata",
        new=AsyncMock(return_value={}),
    ), patch.object(
        service,
        "_persist_context_compaction_event",
        persist_event,
    ), patch(
        "app.services.ai.session_mcp_tools.apply_session_mcp_tools_to_agent_config",
    ), patch(
        "app.services.ai.agent_service.AuditManager.log_transaction",
        new=AsyncMock(),
    ):
        chunks = [
            chunk
            async for chunk in service.chat_completion_stream(
                messages=[{"role": "user", "content": "测试"}],
                agent_id=None,
                agent_name="主助手",
                version_id=None,
                conversation_id="conversation-1",
                user_info={"user_id": "user-1"},
                api_key=None,
                enable_multi_agent=True,
                debug_options=None,
                permission_options=None,
                knowledge_dataset_ids=None,
                metadata_dataset_ids=None,
            )
        ]

    persist_event.assert_awaited_once()
    assert persist_event.await_args.kwargs["user_id"] == "user-1"
    assert not any("lane_user_id" in str(chunk) for chunk in chunks)
