from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.endpoints.chat import BatchDeleteHistoryRequest, batch_delete_history
from app.services.ai.memory_service import memory_service


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_admin_batch_delete_clears_each_conversation_under_its_real_owner(monkeypatch):
    """管理员删除他人会话时，Redis key 必须使用历史记录的真实 user_id。"""
    rows = [
        SimpleNamespace(trace_id="t1", user_id="101", conversation_id="c1"),
        SimpleNamespace(trace_id="t2", user_id="202", conversation_id="c2"),
    ]
    select_result = MagicMock()
    select_result.all.return_value = rows
    db = AsyncMock()
    db.execute.side_effect = [select_result, MagicMock(), MagicMock()]
    clear_mock = AsyncMock()
    monkeypatch.setattr(memory_service, "clear_history", clear_mock)
    request = SimpleNamespace(
        state=SimpleNamespace(user={"user_id": "admin-1", "user_name": "admin", "role": "admin"})
    )

    await batch_delete_history(
        BatchDeleteHistoryRequest(conversation_ids=["c1", "c2"]),
        request,
        db,
    )

    assert {call.args for call in clear_mock.await_args_list} == {("101", "c1"), ("202", "c2")}
