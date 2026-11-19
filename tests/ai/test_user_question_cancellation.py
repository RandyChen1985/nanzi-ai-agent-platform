"""Regression tests for the server-side user-question cancellation short circuit."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.agent_service import AgentService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_cancelled_user_question_stops_before_agent_resolution():
    class FakeQuestionStore:
        async def submit_answer(self, **kwargs):
            assert kwargs["cancelled"] is True
            return {"status": "cancelled"}

    service = AgentService()
    receipt = (
        "【用户回答】\n"
        "interaction_type: question\n"
        "question_id: uq_cancel\n"
        "selected_option_ids: []\n"
        "custom_input: \n"
        "cancelled: true"
    )

    with (
        patch.object(service, "_quota_block_message", AsyncMock(return_value=None)),
        patch(
            "app.services.ai.agent_service.memory_service.get_history",
            AsyncMock(return_value=[]),
        ) as get_history,
        patch(
            "app.services.ai.agent_service.memory_service.add_message",
            AsyncMock(),
        ) as add_message,
        patch(
            "app.services.ai.user_question_store.UserQuestionStore.from_runtime",
            AsyncMock(return_value=FakeQuestionStore()),
        ),
        patch(
            "app.services.ai.context_manager.AgentContextManager.resolve_agent_config",
            AsyncMock(),
        ) as resolve_agent_config,
        patch(
            "app.services.ai.agent_service.AuditManager.log_transaction",
            AsyncMock(),
        ),
        patch(
            "app.services.config_service.ConfigService.get",
            AsyncMock(return_value="20"),
        ),
        patch(
            "app.core.redis.get_redis",
            AsyncMock(return_value=None),
        ),
    ):
        chunks = [
            chunk
            async for chunk in service.chat_completion_stream(
                [{"role": "user", "content": receipt}],
                conversation_id="conversation-cancel",
                user_info={"user_id": "100", "role": "admin"},
                enable_multi_agent=False,
            )
        ]

    # 取消之后流水线还会追加不含 content 的收尾 chunk（meta/status 之类），
    # 用 chunks[-1] 取「最后一条可见内容」会被这些空壳挡住，改为按可见内容过滤。
    visible = [c for c in chunks if c.get("content")]
    assert visible[-1]["content"] == "已取消本次提问，本次任务已停止。"
    assert visible[-1]["status"] == "success"
    resolve_agent_config.assert_not_awaited()
    get_history.assert_awaited_once()
    await asyncio.sleep(0)
    # 取消链路除了「用户消息 + 取消答复」两条落库，还可能追加一条取消状态记录，
    # 所以不锁死总次数；真正要守的是「用户消息只写一次、取消答复写了」。
    roles = [str(call.args[2]) for call in add_message.await_args_list if len(call.args) > 2]
    assert roles.count("user") == 1
    assert roles.count("assistant") >= 1
