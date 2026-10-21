"""追问结果保存：analysis_context 必须同时进入兼容 payload 与统一结果栈。"""

from types import SimpleNamespace

import pytest

from app.services.ai.data_query_semantic_intent import DataQuerySemanticIntent
from app.services.ai.memory_service import memory_service
from app.services.ai.runners.chatbi.followup_data import save_last_data_result_for_followups


@pytest.mark.asyncio
async def test_save_followup_persists_analysis_context(monkeypatch) -> None:
    captured: dict[str, dict] = {}

    async def fake_set_last(user_id, conversation_id, payload):
        captured["legacy"] = payload

    async def fake_stack(user_id, conversation_id):
        return []

    async def fake_push_reusable(user_id, conversation_id, payload, **kwargs):
        captured["reusable"] = payload
        return True

    async def fake_push_ref(user_id, conversation_id, payload):
        captured["ref"] = payload

    monkeypatch.setattr(memory_service, "set_last_data_result", fake_set_last)
    monkeypatch.setattr(memory_service, "get_data_result_stack", fake_stack)
    monkeypatch.setattr(memory_service, "push_reusable_result", fake_push_reusable)
    monkeypatch.setattr(memory_service, "push_data_result_ref", fake_push_ref)

    class Runner:
        conversation_id = "conv-1"
        trace_id = "trace-1"
        _standalone_query = "按月看销售额"
        _semantic_intent = DataQuerySemanticIntent(
            metrics=["sales"],
            dimensions=["region"],
            time_range="2026-01",
            grain="month",
        )
        _last_run_state = SimpleNamespace(followup_data_saved=False, current_result_id=None)

        def _current_user_id(self):
            return 7

    await save_last_data_result_for_followups(
        Runner(),
        {"sql": "SELECT 1", "data_source": "mysql_test", "dataset_name": "sales"},
        {"columns": [{"name": "sales"}], "items": [{"sales": 1}]},
    )

    for key in ("legacy", "reusable"):
        context = captured[key]["analysis_context"]
        assert context["metrics"] == ["sales"]
        assert context["dimensions"] == ["region"]
        assert context["time_grain"] == "month"
        assert context["time_range"] == {"expression": "2026-01"}
        assert captured[key]["question"] == "按月看销售额"

    assert captured["ref"]["analysis_context"]["metrics"] == ["sales"]
