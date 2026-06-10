from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.agent_service import AgentService


class _NoopExecutor:
    async def execute(self, messages):
        yield {"content": "ok"}


class _ExternalPendingExecutor:
    async def execute(self, messages):
        yield {
            "type": "external_execution_required",
            "status": "pending",
            "id": "call_ext",
            "external_execution_request_id": "ext_req_1",
        }


async def _noop_audit(*args, **kwargs):
    return None


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_stream_injects_skill_discovery_hint_into_system_prompt():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )

    captured = {}

    async def fake_dispatch(config, *args, **kwargs):
        captured["system_prompt"] = config.system_prompt
        return _NoopExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=_noop_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "帮我处理一个问题"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("content") == "ok" for chunk in chunks)
    assert "/app/data/skills" in captured["system_prompt"]
    assert "list_available_skills" in captured["system_prompt"]
    assert "read_skill_instruction" in captured["system_prompt"]
    assert "扫描该目录下各技能的 SKILL.md" not in captured["system_prompt"]
    assert captured["system_prompt"].endswith("Base prompt")


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chatbi_agent_defers_turn_classification_to_data_executor():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-data",
        agent_name="chatbi",
        agent_display_name="ChatBI",
        model_name="test-model",
        temperature=0,
        system_prompt="Data prompt",
        tools=[],
        capabilities=["data_query"],
    )

    captured = {}

    async def fake_dispatch(config, *args, **kwargs):
        captured["shared_turn"] = kwargs.get("shared_turn")
        return _NoopExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.ai.turn_classifier.resolve_turn_for_session",
            AsyncMock(side_effect=AssertionError("ChatBI turn classification must stay inside DataQueryExecutor")),
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=_noop_audit,
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value=None),
        ),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "那本月呢"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    meta = next(chunk for chunk in chunks if chunk.get("type") == "meta")
    assert meta["turn_type"] == "data_query_request"
    assert meta["turn_type_label"] == "ChatBI 请求类别分析"
    assert captured["shared_turn"] is None


@pytest.mark.asyncio
@pytest.mark.no_infrastructure
async def test_chat_stream_skips_audit_on_external_execution_required():
    service = AgentService()
    agent_config = ChatConfig(
        agent_id="agent-1",
        agent_name="helper",
        agent_display_name="Helper",
        model_name="test-model",
        temperature=0,
        system_prompt="Base prompt",
        tools=[],
    )
    audit_calls: list[tuple] = []

    async def capture_audit(*args, **kwargs):
        audit_calls.append(args)

    async def fake_dispatch(config, *args, **kwargs):
        return _ExternalPendingExecutor()

    with (
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(return_value=(agent_config, None)),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.setup_context",
            AsyncMock(),
        ),
        patch(
            "app.services.memory_config_service.MemoryConfigService.get_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.ai.memory_service.ltm_service.fetch_memory",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.ai.agent_service.AgentDispatcher.dispatch",
            side_effect=fake_dispatch,
        ),
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            side_effect=capture_audit,
        ),
        patch("app.core.config.Settings.SKILLS_DIR", "/app/data/skills"),
    ):
        chunks = []
        async for chunk in service.chat_completion_stream(
            [{"role": "user", "content": "运行外部工具"}],
            user_info={"user_id": "1", "role": "admin", "user_name": "admin"},
            enable_multi_agent=False,
        ):
            chunks.append(chunk)

    assert any(chunk.get("type") == "external_execution_required" for chunk in chunks)
    assert audit_calls == []
