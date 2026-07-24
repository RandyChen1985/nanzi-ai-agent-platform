import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.context import AgentContext, get_current_agent_context, set_agent_context, set_debug_context
from app.services.chatbi_dataset_schema_service import fetch_dataset_schema_core
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.schema_prefetch import auto_invoke_get_dataset_schema
from app.services.ai.runners.chatbi.tool_gate_wrapper import wrap_tools_with_schema_gate
from app.services.ai.runners.chatbi.tool_result_handlers import format_tool_details
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_schema_gate_injects_context_metadata_dataset_ids_over_session_scope():
    captured = {}

    async def fake_schema(**kwargs):
        captured.update(kwargs)
        return "ok"

    set_agent_context(
        AgentContext(
            agent_id="a1",
            agent_name="data",
            metadata_dataset_ids=["1", "2"],
        )
    )
    runner = SimpleNamespace(
        debug_options={
            "resource_scope": {
                "datasets": [{"id": "9", "name": "session_only", "dataset_name": "session_only"}],
            }
        }
    )
    state = DataRunState(requires_fresh_data=True)
    tools = [
        RuntimeToolSpec(
            name="get_dataset_schema",
            description="schema",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=fake_schema,
            permission_scope="read",
        )
    ]

    wrapped = wrap_tools_with_schema_gate(runner, tools, state)
    result = await wrapped[0].callable(keywords="销售")

    assert result == "ok"
    assert captured["keywords"] == "销售"
    assert captured["metadata_dataset_ids"] == ["1", "2"]


@pytest.mark.asyncio
async def test_schema_gate_falls_back_to_session_scope_when_context_empty():
    captured = {}

    async def fake_schema(**kwargs):
        captured.update(kwargs)
        return "ok"

    set_agent_context(
        AgentContext(
            agent_id="a1",
            agent_name="data",
            metadata_dataset_ids=[],
        )
    )
    runner = SimpleNamespace(
        debug_options={
            "resource_scope": {
                "datasets": [{"id": "9", "name": "session_ds", "dataset_name": "session_ds"}],
            }
        }
    )
    state = DataRunState(requires_fresh_data=True)
    tools = [
        RuntimeToolSpec(
            name="get_dataset_schema",
            description="schema",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=fake_schema,
            permission_scope="read",
        )
    ]

    wrapped = wrap_tools_with_schema_gate(runner, tools, state)
    await wrapped[0].callable(keywords="订单")

    assert captured["metadata_dataset_ids"] == ["9"]


@pytest.mark.asyncio
async def test_schema_prefetch_injects_turn_metadata_dataset_ids():
    captured = {}

    async def fake_schema(**kwargs):
        captured.update(kwargs)
        return "schema"

    set_agent_context(AgentContext(agent_id="a1", agent_name="data", metadata_dataset_ids=["1", "2"]))
    tool = RuntimeToolSpec(
        name="get_dataset_schema",
        description="schema",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_schema,
        permission_scope="read",
    )

    async for _ in auto_invoke_get_dataset_schema(
        SimpleNamespace(_increment_step=lambda: None, trace_buffer=[], config=SimpleNamespace(agent_name="data", model_name="m", temperature=0), step_counter=0, _apply_schema_tool_result=lambda *_: None, _format_tool_details=lambda *_: "", _is_schema_fatal=lambda *_: False),
        keywords="销售",
        tools=[tool],
    ):
        pass

    assert captured["metadata_dataset_ids"] == ["1", "2"]


@pytest.mark.asyncio
async def test_sql_gate_uses_prefetched_dataset_name_for_turn_only_scope():
    captured = {}

    async def fake_sql(**kwargs):
        return "ok"

    async def resolve_preflight(*_args, **kwargs):
        captured.update(kwargs)
        return None

    set_agent_context(AgentContext(agent_id="a1", agent_name="data", metadata_dataset_ids=["1"]))
    runner = SimpleNamespace(debug_options={}, _resolve_sql_schema_preflight_error=resolve_preflight)
    state = DataRunState(requires_fresh_data=False)
    state.schema_output = "dataset: orders\ntable_name: order_items"
    tool = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = wrap_tools_with_schema_gate(runner, [tool], state)
    assert await wrapped[0].callable(sql="SELECT 1", data_source="mysql", dataset_name="orders") == "ok"
    assert captured["allowed_dataset_names"] == {"orders"}


def test_schema_tool_log_shows_effective_scope_and_actual_dataset():
    set_agent_context(
        AgentContext(
            agent_id="a1",
            agent_name="data",
            metadata_dataset_ids=["53"],
            metadata_dataset_scope_debug={
                "source": "turn",
                "authorized": [{"id": "53", "name": "pgdemo"}, {"id": "9", "name": "other"}],
                "effective": [{"id": "53", "name": "pgdemo"}],
            },
        )
    )
    runner = SimpleNamespace(
        _is_schema_gate_block=lambda _: False,
        _schema_keywords_from_args=lambda args: args.get("keywords"),
        _is_failed_sql_repeat_gate_block=lambda _: False,
        _is_sql_sandbox_gate_block=lambda _: False,
        _format_sql_result_for_display=lambda value: value,
        _try_parse_json_output=lambda _: None,
        _is_structured_sql_result=lambda _: False,
        _schema_similarity_threshold=0.2,
        _schema_strong_confidence_threshold=lambda _: 0.5,
        _is_sql_repeat_gate_block=lambda _: False,
        _is_sql_static_gate_block=lambda _: False,
        _is_time_range_gate_block=lambda _: False,
        _is_sql_plan_gate_block=lambda _: False,
    )
    state = DataRunState()
    details = format_tool_details(
        runner,
        "get_dataset_schema",
        "--- [Schema:1] type=table dataset=pgdemo table=orders score=0.91 ---",
        state,
        {"keywords": "订单", "metadata_dataset_ids": ["53"]},
    )

    assert "[数据集范围] 本轮限定" in details
    assert "最终生效：53(pgdemo)" in details
    assert "实际命中：pgdemo" in details


@pytest.mark.asyncio
async def test_schema_scope_debug_is_available_when_selected_dataset_is_not_authorized():
    set_agent_context(AgentContext(agent_id="a1", agent_name="data"))
    set_debug_context({
        "metadata_dataset_scope": {
            "source": "turn",
            "request_ids": ["53"],
            "session": [],
        }
    })

    with patch(
        "app.services.metadata_service.MetadataService.search_datasets",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.services.config_service.ConfigService.get",
        new=AsyncMock(return_value="local"),
    ):
        result = await fetch_dataset_schema_core(
            AsyncMock(),
            keywords="订单",
            user_id=1,
            authorized_dataset_ids=["53"],
    )

    assert "No authorized datasets" in result
    scope_debug = get_current_agent_context().metadata_dataset_scope_debug
    assert scope_debug["source"] == "turn"
    assert scope_debug["authorized"] == []
    assert scope_debug["effective"] == []
    assert scope_debug["provider"] == "local"
