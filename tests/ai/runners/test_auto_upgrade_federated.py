import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

from app.schemas.agent import ChatConfig
from app.services.ai.runners.data_agent_runner import DataAgentRunner, UpgradeToFederatedQuery
from app.services.ai.executors.federated_executor import FederatedQueryExecutor
from app.services.ai.data_query_turn_classifier import DataQueryTurnClassification, DataQueryTurnType
from app.services.ai.intent_service import IntentType

pytestmark = pytest.mark.no_infrastructure


class FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, content_text):
        self.content = [FakeBlock(content_text)]
        self.usage = None


class FakeLLMHandle:
    def __init__(self, response_text, streaming=False):
        self.response_text = response_text
        self.streaming = streaming
        self.model_name = "fake-model"
        self.temperature = 0.0
        self.native_model = self

    def __call__(self, *args, **kwargs):
        return FakeResponse(self.response_text)


@pytest.fixture
def test_config():
    return ChatConfig(
        agent_id="test-agent-id",
        agent_name="TestDataAgent",
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a data agent.",
        tools=["execute_sql_query"],
    )


@pytest.mark.asyncio
async def test_auto_upgrade_to_federated_flow(test_config, monkeypatch):
    # 1. 模拟 SQL 报错且提示不属于当前指定的数据集
    
    # 模拟 session lock
    @asynccontextmanager
    async def _noop_session_lock_hold(**kwargs):
        yield True
        
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.agentscope_session_lock.hold",
        _noop_session_lock_hold,
    )
    
    # 模拟 data_agent_runner 里的 resolve_data_query_turn_classification
    classification = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning="Test turn classification",
        requires_fresh_data=True,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )
    async def fake_resolve(*args, **kwargs):
        return classification, None, 0.0
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )

    # Mock DB Query For MetaTable
    # 模拟涉及两个数据集: 'user_ds' 和 'hr_ds'
    class FakeResult:
        def scalars(self):
            return FakeScalars()
            
    class FakeScalars:
        def all(self):
            return ["user_ds", "hr_ds"]

    mock_db_execute = AsyncMock(return_value=FakeResult())
    
    @asynccontextmanager
    async def fake_db_session():
        session = MagicMock()
        session.execute = mock_db_execute
        yield session

    monkeypatch.setattr(
        "app.core.orm.AsyncSessionLocal",
        fake_db_session,
    )

    # Mock sqlglot extract table refs
    monkeypatch.setattr(
        "app.services.sql_query_execution_service.extract_physical_table_refs_from_select_sql",
        lambda sql, dialect: (None, {"user": "users", "hr": "hrmresource"})
    )
    
    # Mock LLM and prefetched schema call
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=FakeLLMHandle("Dummy output"))
    )
    
    runner = DataAgentRunner(config=test_config, trace_id="trace-upgrade", trace_buffer=[])
    
    # Mock _run_native_agent_turn to raise UpgradeToFederatedQuery
    async def mock_run_turn(*args, **kwargs):
        raise UpgradeToFederatedQuery(sql="SELECT * FROM users JOIN hrmresource ON 1=1", datasets={"user_ds", "hr_ds"})
        yield {}

    monkeypatch.setattr(
        runner,
        "_run_native_agent_turn",
        mock_run_turn,
    )

    # Mock fetch_dataset_schema_core to return dummy schema definition
    mock_fetch_schema = AsyncMock(return_value="dataset: user_ds\ntable: users\n---\ndataset: hr_ds\ntable: hrmresource")
    monkeypatch.setattr(
        "app.services.chatbi_dataset_schema_service.fetch_dataset_schema_core",
        mock_fetch_schema,
    )

    # Mock FederatedQueryExecutor.execute to yield dummy output
    async def mock_federated_execute(*args, **kwargs):
        yield {"content": "Federated query result summary."}
        yield {"status": "success"}

    monkeypatch.setattr(
        FederatedQueryExecutor,
        "execute",
        mock_federated_execute,
    )

    events = []
    try:
        async for chunk in runner.execute([{"role": "user", "content": "查一下 users 和 hrmresource"}]):
            events.append(chunk)
    except Exception as exc:
        print("EXCEPTION RAISED:", type(exc), exc)
        import traceback
        traceback.print_exc()

    print("EVENTS GATHERED:", events)

    # Verify upgrade logs are yielded and turn classification is updated
    upgrade_logs = [e for e in events if e.get("type") == "log" and "请求类别升级" in e.get("title", "")]
    assert len(upgrade_logs) == 1
    assert upgrade_logs[0]["turn_type"] == DataQueryTurnType.FEDERATED_DATA_QUERY.value
    assert "自动升级为联邦查询" in upgrade_logs[0]["details"]

    # Verify federated executor execution was run and chunks yielded
    assert any(e.get("content") == "Federated query result summary." for e in events)


@pytest.mark.asyncio
async def test_federated_turn_type_enters_federated_executor_without_cross_wording(test_config, monkeypatch):
    @asynccontextmanager
    async def _noop_session_lock_hold(**kwargs):
        yield True

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.agentscope_session_lock.hold",
        _noop_session_lock_hold,
    )

    classification = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.FEDERATED_DATA_QUERY,
        reasoning="LLM 判断需要跨数据集关联",
        requires_fresh_data=True,
        requires_few_shot=False,
        requires_sql_query=True,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )

    async def fake_resolve(*args, **kwargs):
        return classification, None, 0.0

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )

    async def fake_auto_schema(*args, **kwargs):
        yield {
            "type": "log",
            "title": "自动获取数据集定义",
            "status": "success",
        }
        yield {
            "__schema_output__": (
                "dataset: user_ds\n"
                "table_name: users\n"
                "---\n"
                "dataset: hr_ds\n"
                "table_name: hrmresource"
            )
        }

    runner = DataAgentRunner(config=test_config, trace_id="trace-direct-federated", trace_buffer=[])
    # schema 获取已从 runner 方法迁到 chatbi.schema_prefetch.auto_invoke_get_dataset_schema，
    # patch runner 上的旧方法已经拦不到任何东西（那条路径现在是死代码）。
    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.schema_prefetch.auto_invoke_get_dataset_schema",
        fake_auto_schema,
    )
    monkeypatch.setattr(runner, "_resolve_runtime_tools_from_config", AsyncMock(return_value=[]))
    monkeypatch.setattr(runner, "_resolve_max_steps", AsyncMock(return_value=4))
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=FakeLLMHandle("native fallback should not run")),
    )

    async def fail_native_turn(*args, **kwargs):
        raise AssertionError("FEDERATED_DATA_QUERY should not enter native ReAct path")
        yield {}

    monkeypatch.setattr(runner, "_run_native_agent_turn", fail_native_turn)

    async def mock_federated_execute(self, *args, **kwargs):
        yield {"content": "Federated direct result."}

    monkeypatch.setattr(FederatedQueryExecutor, "execute", mock_federated_execute)

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查一下用户和 HR 信息"}]):
        events.append(chunk)

    assert any(e.get("content") == "Federated direct result." for e in events)
    assert any(
        e.get("type") == "log"
        and "联邦查询" in str(e.get("details", ""))
        and e.get("turn_type") == DataQueryTurnType.FEDERATED_DATA_QUERY.value
        for e in events
    )


