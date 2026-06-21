import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
import duckdb
from contextlib import asynccontextmanager
from types import SimpleNamespace

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
async def test_federated_graceful_degradation(test_config, monkeypatch):
    # 2. 模拟联邦子查询优雅降级 (非主表子查询失败自愈)
    
    # 准备 Mock 数据集
    class FakeDataset:
        def __init__(self, id, name, data_source):
            self.id = id
            self.name = name
            self.data_source = data_source
            
    mock_datasets = {
        "user_ds": FakeDataset(1, "user_ds", "mysql"),
        "hr_ds": FakeDataset(2, "hr_ds", "clickhouse"),
    }
    
    # Mock MetadataService.get_dataset_by_name
    async def mock_get_dataset_by_name(session, dataset_name):
        return mock_datasets.get(dataset_name)
    monkeypatch.setattr(
        "app.services.ai.executors.federated_executor.MetadataService.get_dataset_by_name",
        mock_get_dataset_by_name
    )

    # Mock PermissionService check_permission (始终允许)
    monkeypatch.setattr(
        "app.services.ai.executors.federated_executor.PermissionService.check_permission",
        AsyncMock(return_value=True)
    )

    # Mock execute_sql_query_core
    # 主表子查询 (user_ds) 成功，第二个子查询 (hr_ds) 报错
    async def mock_execute_sql_query_core(session, sql, data_source, dataset_name, **kwargs):
        if dataset_name == "user_ds":
            return json.dumps({
                "columns": [{"name": "user_id"}, {"name": "user_name"}],
                "items": [[1, "Alice"], [2, "Bob"]]
            })
        else:
            # 模拟第二个子查询报错
            raise ValueError("[TOOL_ERROR] ClickHouse connection timeout")
            
    monkeypatch.setattr(
        "app.services.ai.executors.federated_executor.execute_sql_query_core",
        mock_execute_sql_query_core
    )

    # Mock LLM generating the XML execution plan
    # 两个子查询：
    # 1. SELECT user_id, user_name FROM users (temp_table: users_tbl)
    # 2. SELECT id AS hr_id, name AS hr_name FROM hrmresource (temp_table: hr_tbl)
    # Join SQL: SELECT u.user_id, u.user_name, h.hr_name FROM users_tbl u LEFT JOIN hr_tbl h ON u.user_id = h.hr_id
    plan_xml = """
    <federated_plan>
        <sub_query dataset_name="user_ds" temp_table="users_tbl">
            SELECT user_id, user_name FROM users
        </sub_query>
        <sub_query dataset_name="hr_ds" temp_table="hr_tbl">
            SELECT id AS hr_id, name AS hr_name FROM hrmresource
        </sub_query>
        <memory_join>
            SELECT u.user_id, u.user_name, h.hr_name FROM users_tbl u LEFT JOIN hr_tbl h ON u.user_id = h.hr_id
        </memory_join>
    </federated_plan>
    """
    
    async def mock_get_configured_llm(streaming=False, **kwargs):
        if streaming:
            return FakeLLMHandle("Analysis summary content.", streaming=True)
        else:
            return FakeLLMHandle(plan_xml, streaming=False)

    monkeypatch.setattr(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        mock_get_configured_llm
    )

    @asynccontextmanager
    async def fake_db_session():
        yield MagicMock()
        
    monkeypatch.setattr(
        "app.services.ai.executors.federated_executor.AsyncSessionLocal",
        fake_db_session
    )

    # 实例并运行 FederatedQueryExecutor
    schema_output = "dataset: user_ds\ndata_source: mysql\n---\ndataset: hr_ds\ndata_source: clickhouse"
    
    # mock agent_runner
    runner = MagicMock()
    runner.user_info = {"user_id": 1, "is_admin": True}
    runner.trace_buffer = []
    
    async def fake_save_last_result(*args, **kwargs):
        pass
    runner._save_last_data_result_for_followups = fake_save_last_result

    executor = FederatedQueryExecutor(
        agent_runner=runner,
        schema_output=schema_output,
        datasets=["user_ds", "hr_ds"]
    )

    events = []
    async for chunk in executor.execute(runtime_messages=[], system_prompt="", user_question="查询用户信息和人事信息"):
        events.append(chunk)

    # 验证是否有降级提示气泡与日志
    warning_logs = [e for e in events if e.get("status") == "warning"]
    assert len(warning_logs) > 0
    # 警告提示中应包含 "hr_ds" 相关信息
    assert any("hr_ds" in str(w.get("details", "")) or "hr_ds" in str(w.get("content", "")) for w in warning_logs)

    # 验证最终总结文本被输出了
    assert any(e.get("content") == "Analysis summary content." for e in events)
