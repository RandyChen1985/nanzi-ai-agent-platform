import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.chatbi_result_stack import (
    ChatBIAnalysisContext,
    ChatBIResultRef,
    push_result_ref,
    resolve_result_reference,
)
from app.services.ai.memory_service import MemoryService


@pytest.fixture(scope="function", autouse=True)
async def init_infrastructure():
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis.close_redis", new_callable=AsyncMock), \
         patch("app.core.orm.AsyncSessionLocal", new_callable=MagicMock):
        yield


def _ref(result_id: str, question: str, parent: str | None = None) -> ChatBIResultRef:
    return ChatBIResultRef(
        result_id=result_id,
        parent_result_id=parent,
        question=question,
        dataset_name="sales",
        sql="SELECT 1",
        rows={"rows": [{"value": 1}]},
        analysis_context=ChatBIAnalysisContext(metrics=["销售额"]),
    )


def test_push_result_ref_keeps_ten_newest_and_current():
    stack = []
    for index in range(12):
        stack = push_result_ref(stack, _ref(f"r{index}", f"问题 {index}"))
    assert [item.result_id for item in stack] == [f"r{i}" for i in range(2, 12)]
    assert resolve_result_reference(stack, "当前结果").result.result_id == "r11"


def test_resolve_current_previous_and_explicit_result_reference():
    stack = [_ref("root", "整体销售额"), _ref("region", "按区域分析", "root")]
    assert resolve_result_reference(stack, "这个结果").result.result_id == "region"
    assert resolve_result_reference(stack, "上一个结果").result.result_id == "root"
    assert resolve_result_reference(stack, "result:root").result.result_id == "root"


def test_resolve_bare_result_id_as_explicit_reference():
    stack = [_ref("result_root123", "整体销售额"), _ref("result_region456", "按区域分析")]
    assert resolve_result_reference(stack, "result_root123").result.result_id == "result_root123"


def test_result_ref_from_legacy_payload_ignores_unknown_fields_and_maps_saved_time():
    ref = ChatBIResultRef.from_dict({
        "question": "历史查询",
        "sql": "SELECT 1",
        "saved_at": "2026-07-19T10:00:00",
        "legacy_only": "ignored",
        "filters": [{"field": "region", "value": "华东"}],
    })
    assert ref.question == "历史查询"
    assert ref.created_at == "2026-07-19T10:00:00"
    assert ref.sql == "SELECT 1"


def test_descriptive_reference_returns_ambiguity_instead_of_guessing():
    stack = [_ref("r1", "按区域看销售额"), _ref("r2", "按区域看订单量")]
    resolved = resolve_result_reference(stack, "区域那张表")
    assert resolved.result is None
    assert [item.result_id for item in resolved.candidates] == ["r2", "r1"]


def test_descriptive_reference_can_select_unique_result():
    stack = [_ref("root", "整体销售额"), _ref("region", "按区域分析", "root")]
    resolved = resolve_result_reference(stack, "区域那张表")
    assert resolved.result.result_id == "region"


class _FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.mark.asyncio
async def test_memory_service_result_stack_is_isolated_and_prefers_current(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr("app.services.ai.memory_service.get_redis", AsyncMock(return_value=redis))
    service = MemoryService()
    await service.push_data_result_ref("u1", "c1", _ref("root", "整体销售额").to_dict())
    await service.push_data_result_ref("u1", "c1", _ref("region", "按区域分析", "root").to_dict())
    await service.push_data_result_ref("u2", "c1", _ref("other", "其他用户结果").to_dict())

    stack = await service.get_data_result_stack("u1", "c1")
    current = await service.get_current_data_result("u1", "c1")
    assert [item["result_id"] for item in stack] == ["root", "region"]
    assert current["result_id"] == "region"
    assert current["rows"] == {"rows": [{"value": 1}]}


@pytest.mark.asyncio
async def test_followup_save_dual_writes_legacy_and_structured_result(monkeypatch):
    from app.services.ai.data_query_semantic_intent import DataQuerySemanticIntent
    from app.services.ai.runners.chatbi.followup_data import save_last_data_result_for_followups

    legacy = AsyncMock()
    push = AsyncMock()
    monkeypatch.setattr("app.services.ai.memory_service.memory_service.set_last_data_result", legacy)
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_data_result_stack",
        AsyncMock(return_value=[{"result_id": "parent"}]),
    )
    monkeypatch.setattr("app.services.ai.memory_service.memory_service.push_data_result_ref", push)
    runner = SimpleNamespace(
        conversation_id="conv-1",
        trace_id="trace-1",
        _current_user_id=lambda: 7,
        _standalone_query="查询本月各区域销售额",
        _semantic_intent=DataQuerySemanticIntent(
            metrics=["销售额"],
            dimensions=["区域"],
            time_range="本月",
            grain="day",
        ),
        _last_run_state=SimpleNamespace(followup_data_saved=False),
    )
    await save_last_data_result_for_followups(
        runner,
        {"sql": "SELECT region, SUM(amount) FROM sales", "dataset_name": "sales"},
        {"rows": [{"region": "华东", "amount": 10}]},
    )

    legacy.assert_awaited_once()
    payload = push.await_args.args[2]
    assert payload["parent_result_id"] == "parent"
    assert payload["analysis_context"]["metrics"] == ["销售额"]
    assert payload["analysis_context"]["dimensions"] == ["区域"]
    assert payload["freshness"] == "dynamic"
    assert payload["source_ref"] == "dataset://sales"
    assert payload["observed_at"]
    assert payload["result_status"] == "success_non_empty"
    assert runner._last_run_state.followup_data_saved is True
