import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.core.context import AgentContext, set_agent_context
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi.tool_gate_wrapper import wrap_tools_with_schema_gate
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
