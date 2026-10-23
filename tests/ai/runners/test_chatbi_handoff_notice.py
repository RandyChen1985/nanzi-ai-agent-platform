import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.runners.chatbi.handoff import stream_to_routed_assistant


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


@pytest.mark.asyncio
async def test_handoff_emits_notice_before_delegated_content_without_relabeling_message():
    config = SimpleNamespace(agent_name="general-assistant", agent_display_name="通用智能助手", capabilities=[])
    runner = SimpleNamespace(
        user_info={"user_id": "1"}, trace_id="trace-12345678", trace_buffer=[], execution_options={},
        permission_options={}, conversation_id="conv-1", step_counter=0,
        config=SimpleNamespace(agent_display_name="数据智能助手", agent_name="chat-bi"),
        _runtime_agent_name=lambda: "chat-bi",
    )

    class Executor:
        step_counter = 2
        async def execute(self, _messages):
            yield {"content": "这是接力回答正文"}

    with patch(
        "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
        AsyncMock(return_value=(config, SimpleNamespace(reasoning="通用写作请求"))),
    ), patch(
        "app.services.ai.dispatcher.AgentDispatcher.dispatch",
        AsyncMock(return_value=Executor()),
    ):
        chunks = [chunk async for chunk in stream_to_routed_assistant(
            runner, history=[{"role": "user", "content": "帮我写邮件"}],
            user_question="帮我写邮件", reason="该请求属于通用写作",
        )]

    notice_index = next(i for i, chunk in enumerate(chunks) if chunk.get("type") == "agent_handoff")
    content_index = next(i for i, chunk in enumerate(chunks) if chunk.get("content"))
    assert notice_index < content_index
    assert chunks[notice_index]["data"] == {
        "version": 1, "from_agent": "chat-bi", "from_display_name": "数据智能助手",
        "to_agent": "general-assistant", "to_display_name": "通用智能助手",
        "reason_label": "该请求属于通用写作",
    }
    assert all(chunk.get("type") != "meta" for chunk in chunks)
