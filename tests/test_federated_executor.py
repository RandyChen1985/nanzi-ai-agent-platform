import sys
import os
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.ai.executors.federated_executor import FederatedQueryExecutor, make_markdown_table
from app.core.context import AgentContext, agent_context, set_agent_context

pytestmark = pytest.mark.no_infrastructure


@pytest.fixture(autouse=True)
def mock_search_datasets_globally(monkeypatch):
    async def fake_search_datasets(*args, **kwargs):
        return []
    async def fake_load_column_term_map(*args, **kwargs):
        return {}
    monkeypatch.setattr(
        "app.services.metadata_service.MetadataService.search_datasets",
        fake_search_datasets
    )
    monkeypatch.setattr(
        "app.services.ai.executors.federated_executor.load_column_term_map_for_datasets",
        fake_load_column_term_map
    )



def test_parse_federated_plan_robustness():
    # 构造一个测试执行器
    runner = MagicMock()
    runner.user_info = {}
    runner.trace_buffer = []
    
    executor = FederatedQueryExecutor(runner, "", [])
    
    # 1. 测试带 CDATA 包裹的标准格式
    xml_content_1 = """
    <multi_dataset_plan>
      <sub_query dataset_name="energy_usage" temp_table="t_energy">
        <![CDATA[
        SELECT id, power FROM energy WHERE time > '2026-06-01'
        ]]>
      </sub_query>
      <sub_query dataset_name="asset_info" temp_table="t_asset">
        <![CDATA[
        SELECT id, name FROM asset WHERE status = 1
        ]]>
      </sub_query>
      <memory_join>
        <![CDATA[
        SELECT a.name, e.power FROM t_asset a JOIN t_energy e ON a.id = e.id
        ]]>
      </memory_join>
    </multi_dataset_plan>
    """
    
    subs, join_sql = executor._parse_federated_plan(xml_content_1)
    assert len(subs) == 2
    assert subs[0]["dataset_name"] == "energy_usage"
    assert subs[0]["temp_table"] == "t_energy"
    assert "SELECT id, power" in subs[0]["sql"]
    assert subs[1]["dataset_name"] == "asset_info"
    assert subs[1]["temp_table"] == "t_asset"
    assert "SELECT id, name" in subs[1]["sql"]
    assert "SELECT a.name" in join_sql

    # 2. 测试带 HTML 转义的非 CDATA 格式
    xml_content_2 = """
    <multi_dataset_plan>
      <sub_query dataset_name="energy_usage" temp_table="t_energy">
        SELECT id, power FROM energy WHERE time &gt; '2026-06-01'
      </sub_query>
      <memory_join>
        SELECT * FROM t_energy
      </memory_join>
    </multi_dataset_plan>
    """
    subs, join_sql = executor._parse_federated_plan(xml_content_2)
    assert len(subs) == 1
    assert subs[0]["dataset_name"] == "energy_usage"
    assert "time > '2026-06-01'" in subs[0]["sql"]
    assert join_sql == "SELECT * FROM t_energy"


def test_make_markdown_table():
    cols = [{"name": "id"}, {"name": "name"}]
    rows = [[1, "DeviceA"], [2, "DeviceB"]]
    md = make_markdown_table(cols, rows)
    assert "| id | name |" in md
    assert "| --- | --- |" in md
    assert "| 1 | DeviceA |" in md
    assert "| 2 | DeviceB |" in md


def test_make_markdown_table_with_column_labels():
    cols = [{"name": "FOLLOW_UP_DATE"}, {"name": "CUSTOMER_NAME"}]
    rows = [["2026-06-19", "科华数据"]]
    labels = {"FOLLOW_UP_DATE": "跟进日期", "CUSTOMER_NAME": "客户名称"}
    md = make_markdown_table(cols, rows, column_labels=labels)
    assert "| 跟进日期 | 客户名称 |" in md
    assert "FOLLOW_UP_DATE" not in md.split("\n")[0]


def test_federated_memory_join_sql_blocks_external_access():
    assert FederatedQueryExecutor._validate_memory_join_sql("SELECT * FROM t_energy") is None

    error = FederatedQueryExecutor._validate_memory_join_sql("SELECT * FROM read_csv_auto('/etc/passwd')")
    assert error
    assert "外部访问" in error

    error = FederatedQueryExecutor._validate_memory_join_sql("COPY t_energy TO '/tmp/leak.csv'")
    assert error
    assert "只允许" in error or "外部访问" in error


def test_federated_memory_join_rejects_columns_not_in_subquery_select():
    temp_schemas = {
        "t_visit_log": [
            "FOLLOW_UP_PERSON",
            "CUSTOMER_NAME",
            "FOLLOW_UP_DATE",
            "FOLLOW_UP_CONTENT",
            "PLAN_CONTENT",
        ],
        "t_sales_info": ["ID", "ACCOUNTNAME", "LASTNAME", "FIRSTNAME"],
    }
    join_sql = """
    SELECT
      s.ACCOUNTNAME AS sales_username,
      v.CUSTOMER_NAME
    FROM t_visit_log v
    INNER JOIN t_sales_info s ON v.FOLLOW_UP_PERSON = s.ID
    ORDER BY v.FOLLOW_UP_DATE DESC, v.ID DESC
    """
    error = FederatedQueryExecutor._validate_memory_join_columns(join_sql, temp_schemas)
    assert error
    assert "未 SELECT 的字段" in error
    assert "v.id" in error.lower()


def test_federated_memory_join_allows_columns_from_subquery_select():
    temp_schemas = {
        "t_visit_log": ["ID", "FOLLOW_UP_PERSON", "FOLLOW_UP_DATE"],
        "t_sales_info": ["ID", "LASTNAME"],
    }
    join_sql = """
    SELECT v.ID, s.LASTNAME
    FROM t_visit_log v
    INNER JOIN t_sales_info s ON v.FOLLOW_UP_PERSON = s.ID
    ORDER BY v.FOLLOW_UP_DATE DESC, v.ID DESC
    """
    assert FederatedQueryExecutor._validate_memory_join_columns(join_sql, temp_schemas) is None


def test_auto_fix_memory_join_order_by_strips_missing_column():
    temp_schemas = {
        "t_visit_log": [
            "FOLLOW_UP_PERSON",
            "CUSTOMER_NAME",
            "FOLLOW_UP_DATE",
        ],
        "t_sales_info": ["ID", "LASTNAME"],
    }
    join_sql = """
    SELECT v.CUSTOMER_NAME, s.LASTNAME
    FROM t_visit_log v
    INNER JOIN t_sales_info s ON v.FOLLOW_UP_PERSON = s.ID
    ORDER BY v.FOLLOW_UP_DATE DESC, v.ID DESC
    """
    fixed = FederatedQueryExecutor._auto_fix_memory_join_order_by(join_sql, temp_schemas)
    assert fixed
    assert "v.ID" not in fixed.upper().replace(" ", "")
    assert FederatedQueryExecutor._validate_memory_join_columns(fixed, temp_schemas) is None


def test_build_temp_table_schemas_from_plan_infers_select_columns():
    sub_queries = [
        {
            "dataset_name": "meta_yes_crm_ds",
            "temp_table": "t_visit_log",
            "sql": (
                "SELECT FOLLOW_UP_PERSON, CUSTOMER_NAME, FOLLOW_UP_DATE "
                "FROM VIEW_AI_VISIT_LOG"
            ),
        }
    ]
    schemas = FederatedQueryExecutor._build_temp_table_schemas_from_plan(
        sub_queries,
        {"meta_yes_crm_ds": "oracle"},
    )
    assert schemas["t_visit_log"] == [
        "FOLLOW_UP_PERSON",
        "CUSTOMER_NAME",
        "FOLLOW_UP_DATE",
    ]


def test_federated_executor_limits_rows_and_maps_duckdb_types():
    rows = [[i] for i in range(1002)]
    limited = FederatedQueryExecutor._limit_rows(rows)

    assert len(limited) == 1000
    assert limited[-1] == [999]
    assert FederatedQueryExecutor._duckdb_type_to_result_type("INTEGER") == "number"
    assert FederatedQueryExecutor._duckdb_type_to_result_type("DOUBLE") == "number"
    assert FederatedQueryExecutor._duckdb_type_to_result_type("BOOLEAN") == "boolean"
    assert FederatedQueryExecutor._duckdb_type_to_result_type("VARCHAR") == "string"


def test_federated_executor_reuses_runner_user_resolvers():
    runner = MagicMock()
    runner.user_info = {"id": "1", "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 42
    runner._current_user_is_admin.return_value = True

    executor = FederatedQueryExecutor(runner, "", [])

    assert executor._current_user_id() == 42
    assert executor._current_user_is_admin() is True


@pytest.mark.asyncio
async def test_cross_dataset_schema_enrichment_filters_unauthorized_targets(monkeypatch):
    from app.services.chatbi_dataset_schema_service import _enrich_with_cross_dataset_schema

    source_table = SimpleNamespace(id=1, physical_name="energy_usage")
    target_dataset = SimpleNamespace(id=2, name="asset_ds", display_name="资产数据", data_source="mysql")
    target_table = SimpleNamespace(
        id=2,
        physical_name="asset_owner",
        dataset_id=2,
        dataset=target_dataset,
        columns=[],
    )

    class FakeScalarResult:
        def all(self):
            return [source_table]

    class FakeExecuteResult:
        def scalars(self):
            return FakeScalarResult()

    class FakeSession:
        async def execute(self, stmt):
            return FakeExecuteResult()

    async def fake_get_cross_dataset_related_tables(session, source_table_ids, **kwargs):
        return [target_table]

    async def fake_config_get(key, default=None):
        return default

    def fake_render_schema(dataset, table, relationships, *, data_source=None):
        return f"dataset: {dataset.name}\ntable_name: {table.physical_name}\ndata_source: {data_source}"

    def fake_format_schema_chunk(index, content):
        return content

    async def deny_permission(self, user_id, resource_type, resource_id):
        return False

    monkeypatch.setattr(
        "app.services.chatbi_dataset_schema_service.MetadataService.get_cross_dataset_related_tables",
        fake_get_cross_dataset_related_tables,
    )
    monkeypatch.setattr(
        "app.services.chatbi_dataset_schema_service.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr(
        "app.services.metadata_rag_service.MetadataRagService.render_table_schema_yaml",
        fake_render_schema,
    )
    monkeypatch.setattr(
        "app.services.schema_chunk_format.format_schema_chunk",
        fake_format_schema_chunk,
    )
    monkeypatch.setattr(
        "app.services.permission_service.PermissionService.check_permission",
        deny_permission,
    )

    schema_text = "dataset: energy_ds\ntable_name: energy_usage\ncolumns:\n  - name: device_id"
    enriched_without_permission = await _enrich_with_cross_dataset_schema(
        FakeSession(),
        schema_text,
        user_id=7,
        is_admin=False,
    )
    assert "asset_owner" not in enriched_without_permission

    async def allow_permission(self, user_id, resource_type, resource_id):
        return resource_type == "metadata" and resource_id == "2"

    monkeypatch.setattr(
        "app.services.permission_service.PermissionService.check_permission",
        allow_permission,
    )
    enriched_with_permission = await _enrich_with_cross_dataset_schema(
        FakeSession(),
        schema_text,
        user_id=7,
        is_admin=False,
    )
    assert "asset_owner" in enriched_with_permission


@pytest.mark.asyncio
async def test_federated_executor_execution():
    # 模拟 Runner 及其环境
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = MagicMock()
    
    # 模拟 _save_last_data_result_for_followups 行为
    runner._save_last_data_result_for_followups = AsyncMock()
    
    # 模拟 XML 执行计划返回
    mock_llm_response = """
    <multi_dataset_plan>
      <sub_query dataset_name="energy_ds" temp_table="t_energy">
        SELECT device_id, power FROM energy
      </sub_query>
      <sub_query dataset_name="asset_ds" temp_table="t_asset">
        SELECT id, name FROM asset
      </sub_query>
      <memory_join>
        SELECT a.name, e.power FROM t_asset a JOIN t_energy e ON a.id = e.device_id
      </memory_join>
    </multi_dataset_plan>
    """
    
    # Mock LLM Client
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(return_value=mock_llm_response)
    
    # Mock LLM Stream Client for synthesis
    mock_llm_stream_client = MagicMock()
    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="根据联邦计算结果，")
        yield SimpleNamespace(content="DeviceA 的能耗为 100。")
    mock_llm_stream_client.stream_messages = mock_stream_messages

    # Mock AgentConfigProvider
    mock_plan_handle = object()
    mock_stream_handle = object()
    async def mock_get_llm(streaming=False, *args, **kwargs):
        return mock_stream_handle if streaming else mock_plan_handle

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle is mock_stream_handle else mock_llm_client

    # Mock SQL 执行返回的 JSON 字符串结果
    subquery_1_result = json.dumps({
        "columns": [{"name": "device_id", "type": "str"}, {"name": "power", "type": "int"}],
        "items": [[1, 100], [2, 200]]
    })
    subquery_2_result = json.dumps({
        "columns": [{"name": "id", "type": "str"}, {"name": "name", "type": "str"}],
        "items": [[1, "DeviceA"], [2, "DeviceB"]]
    })
    
    # Mock Metadata
    mock_energy_ds = MagicMock()
    mock_energy_ds.id = 101
    mock_energy_ds.name = "energy_ds"
    mock_energy_ds.data_source = "ck_source"
    
    mock_asset_ds = MagicMock()
    mock_asset_ds.id = 102
    mock_asset_ds.name = "asset_ds"
    mock_asset_ds.data_source = "mysql_source"
    
    # Mock MetadataService
    async def mock_get_dataset_by_name(session, name):
        if name == "energy_ds":
            return mock_energy_ds
        if name == "asset_ds":
            return mock_asset_ds
        return None

    # Mock PermissionService
    async def mock_check_permission(user_id, res_type, res_id):
        # 假设只允许访问 101 和 102
        return res_id in ("101", "102")

    # Mock TraceSpanContext
    mock_span = MagicMock()
    mock_span.set_output = MagicMock()
    
    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return mock_span
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    executor = FederatedQueryExecutor(runner, "Mock Schema Output", ["energy_ds", "asset_ds"])
    
    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core") as mock_execute_sql:
         
         # Mock execute_sql_query_core 的多次返回
         mock_execute_sql.side_effect = [subquery_1_result, subquery_2_result]
         
         # 准备 Mock session
         mock_session = MagicMock()
         mock_session_cls.return_value.__aenter__.return_value = mock_session
         
         # 执行联邦查询
         generator = executor.execute([], "Mock System Prompt", "查看能耗和资产名称的关联结果")
         
         chunks = []
         async for chunk in generator:
             chunks.append(chunk)
             
         # 校验日志和结果
         logs = [c for c in chunks if c.get("type") == "log"]
         contents = [c for c in chunks if c.get("content") is not None]
         
         success_logs = [log for log in logs if log.get("status") == "success"]

         # 应该有 4 个完成日志：计划生成、2个子查询、Join
         assert len(success_logs) == 4
         assert any(l["title"] == "生成跨源联邦查询计划" and l["status"] == "success" for l in logs)
         assert any("t_energy" in l["details"] and l["status"] == "success" for l in logs)
         assert any("t_asset" in l["details"] and l["status"] == "success" for l in logs)
         assert any(l["title"] == "内存联邦聚合计算" and l["status"] == "success" for l in logs)
         
         # 校验最终输出内容
         full_content = "".join([c["content"] for c in contents])
         assert "DeviceA 的能耗为 100" in full_content
         
         # 校验数据暂存是否成功
         runner._save_last_data_result_for_followups.assert_called_once()
         call_args = runner._save_last_data_result_for_followups.call_args[0]
         assert call_args[0]["sql"] == "SELECT a.name, e.power FROM t_asset a JOIN t_energy e ON a.id = e.device_id"
         assert call_args[0]["data_source"] == "duckdb"
         assert call_args[0]["dataset_name"] == "federated"
         
         # 检查暂存的数据内容
         final_saved_data = call_args[1]
         assert final_saved_data["columns"] == [{"name": "name", "type": "string"}, {"name": "power", "type": "number"}]
         assert final_saved_data["items"] == [["DeviceA", 100], ["DeviceB", 200]]


@pytest.mark.asyncio
async def test_federated_executor_repairs_failed_primary_subquery_before_failing():
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = MagicMock()
    runner._save_last_data_result_for_followups = AsyncMock()

    bad_plan = """
    <multi_dataset_plan>
      <sub_query dataset_name="crm_ds" temp_table="t_visit">
        <![CDATA[
        SELECT ID, FOLLOW_UP_DATE FROM VIEW_AI_VISIT_LOG
        WHERE TO_CHAR(FOLLOW_UP_DATE, 'YYYY-MM') = '2026-05'
        ]]>
      </sub_query>
      <memory_join>
        <![CDATA[
        SELECT ID, FOLLOW_UP_DATE FROM t_visit
        ]]>
      </memory_join>
    </multi_dataset_plan>
    """
    repaired_subquery_sql = """
<fixed_sql><![CDATA[
SELECT ID, FOLLOW_UP_DATE FROM VIEW_AI_VISIT_LOG
WHERE FOLLOW_UP_DATE >= DATE '2026-05-01'
  AND FOLLOW_UP_DATE < DATE '2026-06-01'
]]></fixed_sql>
"""
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(side_effect=[bad_plan, repaired_subquery_sql])
    mock_llm_stream_client = MagicMock()

    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="5 月拜访记录已查询完成。")

    mock_llm_stream_client.stream_messages = mock_stream_messages

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "stream" if streaming else "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle == "stream" else mock_llm_client

    mock_ds = MagicMock()
    mock_ds.id = 101
    mock_ds.name = "crm_ds"
    mock_ds.data_source = "oracle_crm"

    async def mock_get_dataset_by_name(session, name):
        return mock_ds

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    sql_error = (
        "[TOOL_ERROR] 本地执行 SQL 失败，错误信息: ORA-01722: invalid number\n\n"
        "[Executed SQL]:\n"
        "SELECT ID, FOLLOW_UP_DATE FROM VIEW_AI_VISIT_LOG "
        "WHERE TO_CHAR(FOLLOW_UP_DATE, 'YYYY-MM') = '2026-05'"
    )
    repaired_result = json.dumps({
        "columns": [{"name": "ID", "type": "number"}, {"name": "FOLLOW_UP_DATE", "type": "date"}],
        "items": [[1, "2026-05-06"]],
    })

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", AsyncMock(side_effect=[sql_error, "[TOOL_ERROR] explain error", repaired_result])):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["crm_ds"]).execute([], "", "查询 2026 年 5 月拜访记录"):
            chunks.append(chunk)

    assert mock_llm_client.generate_text.await_count == 2
    assert any(
        chunk.get("type") == "log"
        and chunk.get("title") == "修复联邦子查询 SQL"
        and chunk.get("status") == "warning"
        for chunk in chunks
    )
    assert not any("跨源联邦主表子查询失败" in str(chunk.get("content") or "") for chunk in chunks)
    assert any(chunk.get("title") == "内存联邦聚合计算" and chunk.get("status") == "success" for chunk in chunks)
    assert "5 月拜访记录已查询完成。" in "".join(chunk.get("content") or "" for chunk in chunks)


@pytest.mark.asyncio
async def test_federated_executor_passes_agent_context_to_sql_core():
    runner = MagicMock()
    runner.user_info = {"id": 7, "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 7
    runner._current_user_is_admin.return_value = False
    runner._save_last_data_result_for_followups = AsyncMock()

    ctx = AgentContext(
        agent_id="agent-1",
        agent_name="DataAgent",
        user_id=7,
        conversation_id="conv-1",
        user_dimensions={"dept_code": "D001", "org_path": "/root/D001"},
    )
    set_agent_context(ctx)

    mock_llm_response = """
    <multi_dataset_plan>
      <sub_query dataset_name="crm_ds" temp_table="t_visit">SELECT ID FROM VIEW_AI_VISIT_LOG</sub_query>
      <memory_join>SELECT ID FROM t_visit</memory_join>
    </multi_dataset_plan>
    """
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(return_value=mock_llm_response)
    mock_llm_stream_client = MagicMock()

    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="ok")

    mock_llm_stream_client.stream_messages = mock_stream_messages

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "stream" if streaming else "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle == "stream" else mock_llm_client

    mock_ds = MagicMock()
    mock_ds.id = 101
    mock_ds.name = "crm_ds"
    mock_ds.data_source = "oracle_crm"

    async def mock_get_dataset_by_name(session, name):
        return mock_ds

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    execute_mock = AsyncMock(return_value=json.dumps({
        "columns": [{"name": "ID", "type": "number"}],
        "items": [[1]],
    }))

    try:
        with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
             patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
             patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
             patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
             patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
             patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
             patch("app.services.ai.executors.federated_executor.execute_sql_query_core", execute_mock):
            mock_session_cls.return_value.__aenter__.return_value = MagicMock()

            async for _ in FederatedQueryExecutor(runner, "", ["crm_ds"]).execute([], "", "test"):
                pass
    finally:
        agent_context.set(None)

    _, kwargs = execute_mock.await_args
    assert kwargs["agent_context"] is ctx
    assert kwargs["user_dimensions"] == {"dept_code": "D001", "org_path": "/root/D001"}


@pytest.mark.asyncio
async def test_federated_executor_permission_failure_does_not_degrade_secondary_query():
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 1
    runner._current_user_is_admin.return_value = False
    runner._save_last_data_result_for_followups = AsyncMock()

    mock_llm_response = """
    <multi_dataset_plan>
      <sub_query dataset_name="user_ds" temp_table="t_user">SELECT id FROM users</sub_query>
      <sub_query dataset_name="hr_ds" temp_table="t_hr">SELECT id FROM hrmresource</sub_query>
      <memory_join>SELECT u.id FROM t_user u LEFT JOIN t_hr h ON u.id = h.id</memory_join>
    </multi_dataset_plan>
    """
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(return_value=mock_llm_response)

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_client

    datasets = {
        "user_ds": SimpleNamespace(id=101, name="user_ds", data_source="mysql_user"),
        "hr_ds": SimpleNamespace(id=102, name="hr_ds", data_source="mysql_hr"),
    }

    async def mock_get_dataset_by_name(session, name):
        return datasets[name]

    async def mock_check_permission(user_id, res_type, res_id):
        return res_id == "101"

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    async def mock_execute_sql_query_core(session, sql, data_source, dataset_name, **kwargs):
        return json.dumps({"columns": [{"name": "id"}], "items": [[1]]})

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", mock_execute_sql_query_core):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["user_ds", "hr_ds"]).execute([], "", "test"):
            chunks.append(chunk)

    combined = "\n".join(str(chunk.get("details") or chunk.get("content") or "") for chunk in chunks)
    assert "无权访问数据集" in combined
    assert "已自动降级" not in combined
    assert not any(chunk.get("title") == "内存联邦聚合计算" and chunk.get("status") == "success" for chunk in chunks)


@pytest.mark.asyncio
async def test_federated_executor_repairs_failed_secondary_subquery_before_degrading():
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 1
    runner._current_user_is_admin.return_value = False
    runner._save_last_data_result_for_followups = AsyncMock()

    bad_plan = """
    <multi_dataset_plan>
      <sub_query dataset_name="user_ds" temp_table="t_user">SELECT id FROM users</sub_query>
      <sub_query dataset_name="hr_ds" temp_table="t_hr">SELECT bad_col FROM hrmresource</sub_query>
      <memory_join>SELECT u.id, h.name FROM t_user u LEFT JOIN t_hr h ON u.id = h.id</memory_join>
    </multi_dataset_plan>
    """
    repaired_subquery_sql = """<fixed_sql><![CDATA[SELECT id, name FROM hrmresource]]></fixed_sql>"""
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(side_effect=[bad_plan, repaired_subquery_sql])
    mock_llm_stream_client = MagicMock()

    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="secondary repaired")

    mock_llm_stream_client.stream_messages = mock_stream_messages

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "stream" if streaming else "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle == "stream" else mock_llm_client

    datasets = {
        "user_ds": SimpleNamespace(id=101, name="user_ds", data_source="mysql_user"),
        "hr_ds": SimpleNamespace(id=102, name="hr_ds", data_source="mysql_hr"),
    }

    async def mock_get_dataset_by_name(session, name):
        return datasets[name]

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    user_result = json.dumps({"columns": [{"name": "id"}], "items": [[1]]})
    hr_error = "[TOOL_ERROR] 本地执行 SQL 失败，错误信息: Unknown column 'bad_col'"
    hr_result = json.dumps({"columns": [{"name": "id"}, {"name": "name"}], "items": [[1, "Alice"]]})

    # repair 轮的 user_ds 子查询 SQL 与首轮完全一致，会命中子查询结果缓存而不再重跑，
    # 因此 execute_sql_query_core 只会被调用 3 次：user_ok -> hr_fail -> (repair) hr_ok。
    sql_exec_mock = AsyncMock(side_effect=[user_result, hr_error, "[TOOL_ERROR] explain error", hr_result])
    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", sql_exec_mock):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["user_ds", "hr_ds"]).execute([], "", "test"):
            chunks.append(chunk)

    assert sql_exec_mock.await_count == 4
    assert mock_llm_client.generate_text.await_count == 2
    assert any(chunk.get("title") == "修复联邦子查询 SQL" for chunk in chunks)
    assert not any("已自动降级" in str(chunk.get("details") or chunk.get("content") or "") for chunk in chunks)
    assert "secondary repaired" in "".join(chunk.get("content") or "" for chunk in chunks)


@pytest.mark.asyncio
async def test_federated_executor_security_error_does_not_degrade_secondary_query():
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 1
    runner._current_user_is_admin.return_value = False
    runner._save_last_data_result_for_followups = AsyncMock()

    mock_llm_response = """
    <multi_dataset_plan>
      <sub_query dataset_name="user_ds" temp_table="t_user">SELECT id FROM users</sub_query>
      <sub_query dataset_name="hr_ds" temp_table="t_hr">SELECT id FROM hrmresource</sub_query>
      <memory_join>SELECT u.id FROM t_user u LEFT JOIN t_hr h ON u.id = h.id</memory_join>
    </multi_dataset_plan>
    """
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(return_value=mock_llm_response)

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_client

    datasets = {
        "user_ds": SimpleNamespace(id=101, name="user_ds", data_source="mysql_user"),
        "hr_ds": SimpleNamespace(id=102, name="hr_ds", data_source="mysql_hr"),
    }

    async def mock_get_dataset_by_name(session, name):
        return datasets[name]

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    user_result = json.dumps({"columns": [{"name": "id"}], "items": [[1]]})
    security_error = "[Security Error] Failed to apply data permissions: boom"

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", AsyncMock(side_effect=[user_result, security_error])):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["user_ds", "hr_ds"]).execute([], "", "test"):
            chunks.append(chunk)

    combined = "\n".join(str(chunk.get("details") or chunk.get("content") or "") for chunk in chunks)
    assert "[Security Error]" in combined
    assert "已自动降级" not in combined
    assert mock_llm_client.generate_text.await_count == 1
    assert not any(chunk.get("title") == "内存联邦聚合计算" and chunk.get("status") == "success" for chunk in chunks)


@pytest.mark.asyncio
async def test_federated_executor_repairs_performance_blocked_secondary_query():
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 1
    runner._current_user_is_admin.return_value = False
    runner._save_last_data_result_for_followups = AsyncMock()

    bad_plan = """
    <multi_dataset_plan>
      <sub_query dataset_name="user_ds" temp_table="t_user">SELECT id FROM users</sub_query>
      <sub_query dataset_name="hr_ds" temp_table="t_hr">SELECT id FROM hrmresource JOIN dept</sub_query>
      <memory_join>SELECT u.id, h.id AS hr_id FROM t_user u LEFT JOIN t_hr h ON u.id = h.id</memory_join>
    </multi_dataset_plan>
    """
    repaired_subquery_sql = """<fixed_sql><![CDATA[SELECT id FROM hrmresource WHERE status = 1]]></fixed_sql>"""
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(side_effect=[bad_plan, repaired_subquery_sql])
    mock_llm_stream_client = MagicMock()

    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="performance repaired")

    mock_llm_stream_client.stream_messages = mock_stream_messages

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "stream" if streaming else "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle == "stream" else mock_llm_client

    datasets = {
        "user_ds": SimpleNamespace(id=101, name="user_ds", data_source="mysql_user"),
        "hr_ds": SimpleNamespace(id=102, name="hr_ds", data_source="mysql_hr"),
    }

    async def mock_get_dataset_by_name(session, name):
        return datasets[name]

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    user_result = json.dumps({"columns": [{"name": "id"}], "items": [[1]]})
    performance_error = "[Performance Blocked] SQL 包含没有关联条件的 JOIN 操作"
    hr_result = json.dumps({"columns": [{"name": "id"}], "items": [[1]]})

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", AsyncMock(side_effect=[user_result, performance_error, "[TOOL_ERROR] explain error", hr_result])):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["user_ds", "hr_ds"]).execute([], "", "test"):
            chunks.append(chunk)

    assert mock_llm_client.generate_text.await_count == 2
    assert any(chunk.get("title") == "修复联邦子查询 SQL" for chunk in chunks)
    assert not any("已自动降级" in str(chunk.get("details") or chunk.get("content") or "") for chunk in chunks)
    assert "performance repaired" in "".join(chunk.get("content") or "" for chunk in chunks)


@pytest.mark.asyncio
async def test_federated_executor_sandboxes_and_closes_duckdb(monkeypatch):
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = MagicMock()
    runner._save_last_data_result_for_followups = AsyncMock()

    mock_llm_response = """
    <multi_dataset_plan>
      <sub_query dataset_name="energy_ds" temp_table="t_energy">SELECT device_id FROM energy</sub_query>
      <memory_join>SELECT device_id FROM t_energy</memory_join>
    </multi_dataset_plan>
    """
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(return_value=mock_llm_response)
    mock_llm_stream_client = MagicMock()

    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="ok")

    mock_llm_stream_client.stream_messages = mock_stream_messages

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "stream" if streaming else "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle == "stream" else mock_llm_client

    mock_ds = MagicMock()
    mock_ds.id = 101
    mock_ds.name = "energy_ds"
    mock_ds.data_source = "ck_source"

    async def mock_get_dataset_by_name(session, name):
        return mock_ds

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_conn = MagicMock()
    fake_res = MagicMock()
    fake_res.fetchall.return_value = [[1]]
    fake_res.description = [("device_id", "INTEGER")]
    fake_conn.execute.return_value = fake_res

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.duckdb.connect", return_value=fake_conn) as mock_connect, \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", AsyncMock(return_value=json.dumps({"columns": [{"name": "device_id"}], "items": [[1]]}))):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["energy_ds"]).execute([], "", "test"):
            chunks.append(chunk)

    mock_connect.assert_called_once_with(
        database=":memory:",
        config={"enable_external_access": False},
    )
    fake_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_federated_executor_rejects_dangerous_memory_join_before_execute():
    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = MagicMock()
    runner._save_last_data_result_for_followups = AsyncMock()

    mock_llm_response = """
    <multi_dataset_plan>
      <sub_query dataset_name="energy_ds" temp_table="t_energy">SELECT device_id FROM energy</sub_query>
      <memory_join>SELECT * FROM read_csv_auto('/etc/passwd')</memory_join>
    </multi_dataset_plan>
    """
    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(return_value=mock_llm_response)

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_client

    mock_ds = MagicMock()
    mock_ds.id = 101
    mock_ds.name = "energy_ds"
    mock_ds.data_source = "ck_source"

    async def mock_get_dataset_by_name(session, name):
        return mock_ds

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    fake_conn = MagicMock()

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.duckdb.connect", return_value=fake_conn), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", AsyncMock(return_value=json.dumps({"columns": [{"name": "device_id"}], "items": [[1]]}))):
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["energy_ds"]).execute([], "", "test"):
            chunks.append(chunk)

    assert any("禁止外部访问" in str(chunk.get("details") or chunk.get("content") or "") for chunk in chunks)
    fake_conn.execute.assert_not_called()
    fake_conn.close.assert_called_once()


@pytest.mark.asyncio
async def test_federated_executor_repairs_subquery_time_range_mismatch_before_execute():
    """子查询 SQL 使用错误年份的「上个月」日期时，应在执行前拦截并触发联邦 repair。"""
    from datetime import datetime

    import pytz

    from app.services.ai.time_anchor import detect_time_range_mismatch

    tz = pytz.timezone("Asia/Shanghai")
    fixed_now = tz.localize(datetime(2026, 6, 21, 10, 0, 0))
    user_question = "帮我查询上个月所有销售人员的拜访记录"

    bad_sql = (
        "SELECT follow_up_person, follow_up_date FROM VIEW_AI_VISIT_LOG "
        "WHERE follow_up_date >= TO_DATE('2025-05-01', 'YYYY-MM-DD') "
        "AND follow_up_date < TO_DATE('2025-06-01', 'YYYY-MM-DD')"
    )
    good_sql = (
        "SELECT follow_up_person, follow_up_date FROM VIEW_AI_VISIT_LOG "
        "WHERE follow_up_date >= DATE '2026-05-01' AND follow_up_date < DATE '2026-06-01'"
    )
    assert detect_time_range_mismatch(user_question, bad_sql, now=fixed_now)
    assert detect_time_range_mismatch(user_question, good_sql, now=fixed_now) == ""

    runner = MagicMock()
    runner.user_info = {"id": 1, "role": "user"}
    runner.trace_buffer = []
    runner._current_user_id.return_value = 1
    runner._current_user_is_admin.return_value = False
    runner._save_last_data_result_for_followups = AsyncMock()

    bad_plan = f"""
    <multi_dataset_plan>
      <sub_query dataset_name="crm_ds" temp_table="t_visit">
        <![CDATA[{bad_sql}]]>
      </sub_query>
      <memory_join>
        <![CDATA[SELECT follow_up_person, follow_up_date FROM t_visit]]>
      </memory_join>
    </multi_dataset_plan>
    """
    fixed_subquery_sql = f"<fixed_sql><![CDATA[{good_sql}]]></fixed_sql>"

    mock_llm_client = MagicMock()
    mock_llm_client.generate_text = AsyncMock(side_effect=[bad_plan, fixed_subquery_sql])
    mock_llm_stream_client = MagicMock()

    async def mock_stream_messages(*args, **kwargs):
        yield SimpleNamespace(content="上月拜访记录查询完成")

    mock_llm_stream_client.stream_messages = mock_stream_messages

    async def mock_get_llm(streaming=False, *args, **kwargs):
        return "stream" if streaming else "plan"

    def fake_chat_client_from_handle(handle):
        return mock_llm_stream_client if handle == "stream" else mock_llm_client

    mock_ds = MagicMock()
    mock_ds.id = 101
    mock_ds.name = "crm_ds"
    mock_ds.data_source = "oracle_crm"

    async def mock_get_dataset_by_name(session, name):
        return mock_ds

    async def mock_check_permission(user_id, res_type, res_id):
        return True

    class FakeTraceSpanContext:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            span = MagicMock()
            span.set_output = MagicMock()
            return span

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    ok_result = json.dumps({
        "columns": [{"name": "follow_up_person"}, {"name": "follow_up_date"}],
        "items": [["Alice", "2026-05-10"]],
    })
    execute_mock = AsyncMock(return_value=ok_result)

    with patch("app.services.ai.executors.federated_executor.AgentConfigProvider.get_configured_llm", side_effect=mock_get_llm), \
         patch("app.services.ai.executors.federated_executor.chat_client_from_handle", side_effect=fake_chat_client_from_handle), \
         patch("app.services.ai.executors.federated_executor.AsyncSessionLocal") as mock_session_cls, \
         patch("app.services.permission_service.PermissionService.check_permission", side_effect=mock_check_permission), \
         patch("app.services.metadata_service.MetadataService.get_dataset_by_name", side_effect=mock_get_dataset_by_name), \
         patch("app.services.ai.runtime.agentscope.trace_context.TraceSpanContext", FakeTraceSpanContext), \
         patch("app.services.ai.executors.federated_executor.execute_sql_query_core", execute_mock), \
         patch("app.services.ai.executors.federated_executor.detect_time_range_mismatch") as mock_detect:
        mock_detect.side_effect = lambda q, sql, **kwargs: detect_time_range_mismatch(
            q, sql, now=fixed_now
        )
        mock_session_cls.return_value.__aenter__.return_value = MagicMock()

        chunks = []
        async for chunk in FederatedQueryExecutor(runner, "", ["crm_ds"]).execute([], "", user_question):
            chunks.append(chunk)

    assert execute_mock.await_count == 2
    assert mock_llm_client.generate_text.await_count == 2
    assert any(chunk.get("title") == "修复联邦子查询 SQL" for chunk in chunks)
    assert "上月拜访记录查询完成" in "".join(chunk.get("content") or "" for chunk in chunks)


if __name__ == "__main__":
    pytest.main([__file__])
