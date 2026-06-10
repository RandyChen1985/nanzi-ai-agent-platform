from unittest.mock import MagicMock, patch

import pytest

from app.schemas.agent import ChatConfig
from app.services.ai.executors.data_executor import DataQueryExecutor


pytestmark = pytest.mark.no_infrastructure


@pytest.fixture
def data_config():
    return ChatConfig(
        agent_id="data-agent-id",
        agent_name="DataQueryAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a data assistant. Use {dataset_menu} to see available tables.",
        tools=["get_dataset_schema", "execute_sql_query"],
    )


@pytest.mark.asyncio
async def test_data_executor_delegates_to_data_agent_runner(data_config):
    trace_buffer = []
    executor = DataQueryExecutor(
        config=data_config,
        trace_id="trace-data-native",
        trace_buffer=trace_buffer,
        debug_options={"dry_run": True},
        user_info={"id": "u1"},
        conversation_id="c1",
    )

    runner_instance = MagicMock()
    runner_instance.step_counter = 3

    async def fake_execute(history):
        yield {"content": f"native:{history[0]['content']}"}

    runner_instance.execute = fake_execute

    with patch("app.services.ai.executors.data_executor.DataAgentRunner", return_value=runner_instance) as runner_cls:
        events = []
        async for chunk in executor.execute([{"role": "user", "content": "查数据"}]):
            events.append(chunk)

    assert events == [{"content": "native:查数据"}]
    runner_cls.assert_called_once()
    _, kwargs = runner_cls.call_args
    assert kwargs["config"] is data_config
    assert kwargs["trace_id"] == "trace-data-native"
    assert kwargs["trace_buffer"] is trace_buffer
    assert kwargs["debug_options"] == {"dry_run": True}
    assert kwargs["user_info"] == {"id": "u1"}
    assert kwargs["conversation_id"] == "c1"
    assert executor.step_counter == 3
