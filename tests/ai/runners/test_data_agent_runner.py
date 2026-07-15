import json
import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel

from app.schemas.agent import ChatConfig
from app.services.ai.runtime.agentscope.compat import AIMessage
from app.services.ai.data_query_turn_classifier import DataQueryTurnClassification, DataQueryTurnType
from app.services.ai.intent_service import IntentType
from app.services.ai.grounding.models import EvidenceType


pytestmark = pytest.mark.no_infrastructure


def test_federated_upgrade_requires_explicit_cross_dataset_intent():
    from app.services.ai.runners.data_agent_runner import _should_upgrade_to_federated_query

    schema_output = """
dataset: energy_ds
table_name: energy_usage
columns: device_id, power

# [跨数据集关联补全: asset_ds.asset_info]
dataset: asset_ds
table_name: asset_info
columns: id, owner
"""

    assert _should_upgrade_to_federated_query(
        schema_output,
        user_question="查一下本月能耗超标的设备",
    ) is False
    assert _should_upgrade_to_federated_query(
        schema_output,
        user_question="跨数据集关联能耗数据和资产数据，查维保人员",
    ) is True
    # 避免单数据集内的两表关联被误判升级为联邦查询
    assert _should_upgrade_to_federated_query(
        schema_output,
        user_question="在这个数据集里把这两张表关联查询一下",
    ) is False


@pytest.fixture
def data_config():
    return ChatConfig(
        agent_id="data-agent-id",
        agent_name="DataAgent",
        agent_version=None,
        model_name="gpt-4o",
        temperature=0.0,
        system_prompt="You are a data agent.",
        tools=["update_dashboard_context"],
    )


@pytest.fixture(autouse=True)
def isolate_data_agent_runtime(monkeypatch):
    async def _no_redis():
        return None

    @asynccontextmanager
    async def _noop_session_lock_hold(**kwargs):
        yield True

    monkeypatch.setattr("app.core.redis.get_redis", _no_redis)
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.get_local_workspace",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.agentscope_session_lock.hold",
        _noop_session_lock_hold,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.agent_state_store.load",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.agent_state_store.save",
        AsyncMock(return_value=None),
    )

    async def fake_config_get(key, default=None):
        if key == "agent_max_iterations":
            return "5"
        return default

    fake_llm_handle = SimpleNamespace(
        native_model=SimpleNamespace(model="fake-native-data"),
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        AsyncMock(return_value=fake_llm_handle),
    )

    async def fake_schema(**kwargs):
        return "table_name: demo\ncolumns: id, status, count, total"

    async def fake_sql(**kwargs):
        return [{"id": 1}]

    monkeypatch.setitem(
        __import__("app.services.ai.tools.registry", fromlist=["ToolRegistry"]).ToolRegistry._registry,
        "get_dataset_schema",
        fake_schema,
    )
    monkeypatch.setitem(
        __import__("app.services.ai.tools.registry", fromlist=["ToolRegistry"]).ToolRegistry._registry,
        "execute_sql_query",
        fake_sql,
    )


@pytest.fixture(autouse=True)
def default_data_agent_turn_classification(monkeypatch):
    classification = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning="测试默认：新数据查询",
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


@pytest.mark.asyncio
async def test_data_agent_runner_resolves_chatbi_runtime_tools(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner
    from app.services.ai.tools.registry import ToolRegistry

    runner = DataAgentRunner(config=data_config, trace_id="trace-data", trace_buffer=[])

    tools = await runner._resolve_runtime_tools_from_config()
    tool_names = [tool.name for tool in tools]

    assert tool_names[:3] == [
        "update_dashboard_context",
        "get_dataset_schema",
        "execute_sql_query",
    ]
    assert [tool.permission_scope for tool in tools[:3]] == ["read", "read", "read"]
    system_tool_names = {tool.name for tool in ToolRegistry.get_system_implicit_tools()}
    assert system_tool_names.issubset(set(tool_names))
    assert "get_current_time" in tool_names
    time_tool = next(tool for tool in tools if tool.name == "get_current_time")
    assert time_tool.permission_scope == "read"
    assert time_tool.evidence_types == frozenset({EvidenceType.RUNTIME_STATE})


@pytest.mark.asyncio
async def test_data_agent_runner_auto_invokes_schema_before_react(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    schema_calls: list[dict] = []

    async def fake_schema(**kwargs):
        schema_calls.append(kwargs)
        return "dataset: demo\ntable_name: demo\ncolumns: id"

    async def empty_agent_turn(*args, **kwargs):
        assert schema_calls, "schema prefetch must happen before react"
        if False:
            yield {}

    monkeypatch.setitem(
        __import__("app.services.ai.tools.registry", fromlist=["ToolRegistry"]).ToolRegistry._registry,
        "get_dataset_schema",
        fake_schema,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.DataAgentRunner._run_native_agent_turn",
        empty_agent_turn,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-prefetch", trace_buffer=[])
    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查一下本月商机"}]):
        events.append(chunk)

    assert len(schema_calls) == 1
    assert "商机" in str(schema_calls[0].get("keywords", ""))
    assert any(event.get("title") == "自动获取数据集定义" for event in events)
    assert any(event.get("title") == "工具完成: get_dataset_schema" for event in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output,flag_name",
    [
        (
            __import__(
                "app.services.metadata_rag_service",
                fromlist=["MetadataRagService"],
            ).MetadataRagService.unavailable_hint("RAGFlow HTTP Error 502: "),
            "schema_service_unavailable",
        ),
        (
            "No authorized datasets found. You do not have permission to view any data.",
            "no_authorized_schema",
        ),
        (
            "Authorized datasets found, but none are synced to RAG knowledge base.",
            "rag_not_synced",
        ),
    ],
)
async def test_apply_schema_tool_result_does_not_mark_completed_on_fatal_errors(
    data_config,
    output,
    flag_name,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    state = _DataRunState()
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-apply-schema",
        trace_buffer=[],
    )
    runner._apply_schema_tool_result(state, output)

    assert getattr(state, flag_name) is True
    assert state.schema_completed is False
    assert runner._is_schema_fatal(state) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "schema_output,expected_title,content_snippet",
    [
        (
            __import__(
                "app.services.metadata_rag_service",
                fromlist=["MetadataRagService"],
            ).MetadataRagService.unavailable_hint("RAGFlow HTTP Error 502: "),
            "元数据服务不可用",
            "元数据检索服务当前不可用",
        ),
        (
            "No authorized datasets found. You do not have permission to view any data.",
            "无授权数据集",
            "没有可访问的数据集权限",
        ),
        (
            "Authorized datasets found, but none are synced to RAG knowledge base.",
            "元数据未同步知识库",
            "尚未同步到元数据知识库",
        ),
    ],
)
async def test_data_agent_runner_stops_on_fatal_schema_errors(
    data_config,
    monkeypatch,
    schema_output,
    expected_title,
    content_snippet,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_schema(**kwargs):
        return schema_output

    react_called = False

    async def fake_agent_turn(*args, **kwargs):
        nonlocal react_called
        react_called = True
        if False:
            yield {}

    monkeypatch.setitem(
        __import__("app.services.ai.tools.registry", fromlist=["ToolRegistry"]).ToolRegistry._registry,
        "get_dataset_schema",
        fake_schema,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.DataAgentRunner._run_native_agent_turn",
        fake_agent_turn,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-fatal", trace_buffer=[])
    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查一下智能体用户表"}]):
        events.append(chunk)

    assert react_called is False
    assert any(event.get("title") == expected_title for event in events)
    assert any(content_snippet in str(event.get("content", "")) for event in events if event.get("content"))
    assert not any("必须先执行 SQL 查数" in str(event.get("title", "")) for event in events)


@pytest.mark.asyncio
async def test_data_agent_runner_skips_schema_prefetch_for_context_action(data_config, monkeypatch):
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    context_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.CONTEXT_ACTION,
        reasoning="测试：上下文动作",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.GENERAL,
    )

    async def fake_resolve(*args, **kwargs):
        return context_turn, None, 0.0

    async def fake_schema(**kwargs):
        raise AssertionError("context action must not auto invoke schema")

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setitem(
        __import__("app.services.ai.tools.registry", fromlist=["ToolRegistry"]).ToolRegistry._registry,
        "get_dataset_schema",
        fake_schema,
    )
    async def empty_agent_turn(*args, **kwargs):
        if False:
            yield {}

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.DataAgentRunner._run_native_agent_turn",
        empty_agent_turn,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-no-prefetch", trace_buffer=[])
    events = []
    async for chunk in runner.execute([{"role": "user", "content": "保存上面的结果"}]):
        events.append(chunk)

    assert not any(event.get("title") == "自动获取数据集定义" for event in events)


@pytest.mark.asyncio
async def test_data_agent_runner_builds_chatbi_toolkit_without_workspace_file_tools(
    data_config, monkeypatch
):
    from unittest.mock import MagicMock

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    fake_workspace = MagicMock()
    fake_toolkit = MagicMock()
    build_chatbi = AsyncMock(return_value=(MagicMock(), []))
    build_toolkit = MagicMock(return_value=fake_toolkit)
    captured_agent_kwargs: dict = {}

    def fake_agent(**kwargs):
        captured_agent_kwargs.update(kwargs)
        instance = MagicMock()
        instance.name = "AgentInstance"
        return instance

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.get_local_workspace",
        AsyncMock(return_value=fake_workspace),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_chatbi_toolkit",
        build_chatbi,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_toolkit",
        build_toolkit,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.Agent",
        fake_agent,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        AsyncMock(return_value=None),
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-toolkit", trace_buffer=[])
    tools = await runner._resolve_runtime_tools_from_config()
    agent = await runner._build_native_agent(
        native_model=MagicMock(model="fake"),
        tools=tools,
        system_content="system",
        max_steps=3,
        primary_model_name="fake-model",
    )

    build_chatbi.assert_awaited_once()
    build_toolkit.assert_called_once_with(
        tools,
        approval_mode=runner.permission_options.get("approval_mode"),
    )
    assert captured_agent_kwargs["toolkit"] is fake_toolkit
    assert captured_agent_kwargs["offloader"] is fake_workspace
    assert agent.name == "AgentInstance"


@pytest.mark.asyncio
async def test_data_agent_runner_system_content_includes_data_guardrails(data_config):
    from app.services.ai.executors.prompts import DataQueryPrompts
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-data", trace_buffer=[])

    system_content = await runner._build_system_content()

    assert DataQueryPrompts.GLOBAL_GUARDRAILS in system_content
    assert DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE in system_content
    assert "SQL Server" in system_content
    assert "TOP N" in system_content
    assert "[当前时间锚点]" in system_content
    assert "【相对时间 SQL 规则】" in system_content
    assert DataQueryPrompts.SQL_PLAN_ENFORCEMENT not in system_content
    assert "<sql_plan>" not in system_content
    assert DataQueryPrompts.FOLLOWUP_REUSE_CONSTRAINT in system_content
    assert data_config.system_prompt in system_content


@pytest.mark.asyncio
async def test_data_agent_runner_system_content_includes_sql_plan_when_enabled(data_config):
    from app.services.ai.executors.prompts import DataQueryPrompts
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-data-plan",
        trace_buffer=[],
        debug_options={"enable_sql_plan": True},
    )

    system_content = await runner._build_system_content()

    assert DataQueryPrompts.SQL_PLAN_ENFORCEMENT in system_content
    assert "<sql_plan>" in system_content


@pytest.mark.asyncio
async def test_schema_keyword_planner_stores_structured_semantic_intent(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeModel:
        async def ainvoke(self, messages):
            class Response:
                content = (
                    '{"keywords":"机房 剩余机柜数 上海 区域",'
                    '"goal":"查询上海区域所有机房的剩余机柜数",'
                    '"metrics":["剩余机柜数"],"dimensions":["机房"],'
                    '"filters":[{"phrase":"上海区域","semantic_type":"geographic_region",'
                    '"expected_column_types":["区域","gxqy","region","area"],'
                    '"avoid_column_types":["shipName"],"relation":"parent_region_or_scope"}],'
                    '"time_range":"无","grain":"机房"}'
                )

            return Response()

    async def fake_get_configured_llm(*args, **kwargs):
        return FakeModel()

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    runner = DataAgentRunner(config=data_config, trace_id="trace-data-intent", trace_buffer=[])

    keywords = await runner._plan_schema_search_keywords(
        "查询上海区域所有机房的剩余机柜数",
        "查询上海区域所有机房的剩余机柜数",
        [],
    )

    assert keywords == "机房 剩余机柜数 上海 区域"
    assert runner._semantic_intent is not None
    assert runner._semantic_intent.metrics == ["剩余机柜数"]
    assert runner._semantic_intent.filters[0].phrase == "上海区域"
    assert "gxqy" in runner._semantic_intent.filters[0].expected_column_types


@pytest.mark.asyncio
async def test_schema_keyword_planner_does_not_bypass_short_business_query(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    calls = {"count": 0}

    class FakeModel:
        async def ainvoke(self, messages):
            calls["count"] += 1

            class Response:
                content = (
                    '{"keywords":"上海 机房",'
                    '"goal":"查询上海机房",'
                    '"metrics":[],"dimensions":["机房"],'
                    '"filters":[{"phrase":"上海","semantic_type":"geographic_region",'
                    '"expected_column_types":["区域","gxqy"],'
                    '"avoid_column_types":["shipName"],"relation":"parent_region_or_scope"}]}'
                )

            return Response()

    async def fake_get_configured_llm(*args, **kwargs):
        return FakeModel()

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    runner = DataAgentRunner(config=data_config, trace_id="trace-short-intent", trace_buffer=[])

    keywords = await runner._plan_schema_search_keywords("上海机房", "上海机房", [])

    assert calls["count"] == 1
    assert keywords == "上海 机房"
    assert runner._semantic_intent is not None
    assert runner._semantic_intent.filters[0].relation == "parent_region_or_scope"


@pytest.mark.asyncio
async def test_schema_keyword_planner_derives_keywords_from_intent_when_llm_keywords_invalid(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeModel:
        async def ainvoke(self, messages):
            class Response:
                content = (
                    '{"keywords":"关键词",'
                    '"goal":"查询上海区域所有机房的剩余机柜数",'
                    '"metrics":["剩余机柜数"],"dimensions":["机房"],'
                    '"filters":[{"phrase":"上海区域","semantic_type":"geographic_region",'
                    '"expected_column_types":["区域","gxqy","region","area"],'
                    '"avoid_column_types":["shipName"],"relation":"parent_region_or_scope"}],'
                    '"grain":"机房"}'
                )

            return Response()

    async def fake_get_configured_llm(*args, **kwargs):
        return FakeModel()

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    runner = DataAgentRunner(config=data_config, trace_id="trace-derived-keywords", trace_buffer=[])

    keywords = await runner._plan_schema_search_keywords(
        "查询上海区域所有机房的剩余机柜数",
        "查询上海区域所有机房的剩余机柜数",
        [],
    )

    assert keywords == "剩余机柜数 机房 上海区域 区域 gxqy region area"
    assert runner._semantic_intent is not None
    assert runner._semantic_intent.keywords == keywords


@pytest.mark.asyncio
async def test_schema_keyword_planner_includes_recent_context_in_semantic_intent_prompt(data_config, monkeypatch):
    from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    captured = {}

    class FakeModel:
        async def ainvoke(self, messages):
            captured["prompt"] = getattr(messages[0], "content", "")

            class Response:
                content = (
                    '{"keywords":"北京 机房 剩余机柜数",'
                    '"goal":"查询北京区域所有机房的剩余机柜数",'
                    '"metrics":["剩余机柜数"],"dimensions":["机房"],'
                    '"filters":[{"phrase":"北京区域","semantic_type":"geographic_region",'
                    '"expected_column_types":["区域","gxqy"],'
                    '"avoid_column_types":["shipName"],"relation":"parent_region_or_scope"}]}'
                )

            return Response()

    async def fake_get_configured_llm(*args, **kwargs):
        return FakeModel()

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    runner = DataAgentRunner(config=data_config, trace_id="trace-context-intent", trace_buffer=[])
    runtime_messages = [
        HumanMessage(content="查询上海区域所有机房的剩余机柜数"),
        AIMessage(content="已查询上海区域所有机房的剩余机柜数。"),
        HumanMessage(content="那北京的呢"),
    ]

    keywords = await runner._plan_schema_search_keywords(
        "那北京的呢",
        "查询北京区域所有机房的剩余机柜数",
        [],
        runtime_messages=runtime_messages,
    )

    assert keywords == "北京 机房 剩余机柜数"
    assert "【最近对话上下文】" in captured["prompt"]
    assert "用户: 查询上海区域所有机房的剩余机柜数" in captured["prompt"]
    assert "助手: 已查询上海区域所有机房的剩余机柜数。" in captured["prompt"]
    assert "【最新提问优先级】" in captured["prompt"]


def test_empty_result_repair_includes_structured_semantic_intent(data_config):
    from app.services.ai.data_query_semantic_intent import (
        DataQuerySemanticIntent,
        SemanticIntentFilter,
    )
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-intent", trace_buffer=[])
    runner._semantic_intent = DataQuerySemanticIntent(
        goal="查询上海区域所有机房的剩余机柜数",
        keywords="机房 剩余机柜数 上海 区域",
        metrics=["剩余机柜数"],
        dimensions=["机房"],
        filters=[
            SemanticIntentFilter(
                phrase="上海区域",
                semantic_type="geographic_region",
                expected_column_types=["区域", "gxqy", "region", "area"],
                avoid_column_types=["shipName"],
                relation="parent_region_or_scope",
            )
        ],
    )
    state = _DataRunState(
        empty_sql_result=True,
        empty_sql_reason="SQL 执行成功但返回空结果",
        empty_sql_text="SELECT shipName, spareCabinet FROM demo WHERE shipName LIKE '%上海%'",
        empty_filter_diagnostics=[
            {
                "column": "shipName",
                "used_values": ["上海"],
                "candidates": ["外高桥", "金桥B8", "临港123期", "唐镇"],
                "alternative_columns": ["cc_username", "ccname", "gxqy"],
            }
        ],
        empty_filter_diagnostic_summary="【平台自动筛选诊断】候选值未直接包含上海。",
    )

    repair = runner._build_repair_message(state)

    assert "空结果语义复核" in repair
    assert "上海区域" in repair
    assert "父级/范围条件" in repair
    assert "gxqy" in repair
    assert "不能仅因候选值不包含原词就判定无数据" in repair


def test_need_analysis_success_details_includes_structured_semantic_intent(data_config):
    from app.services.ai.data_query_semantic_intent import (
        DataQuerySemanticIntent,
        SemanticIntentFilter,
    )
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-need-analysis-intent", trace_buffer=[])
    runner._semantic_intent = DataQuerySemanticIntent(
        goal="查询上海区域所有机房的剩余机柜数",
        keywords="机房 剩余机柜数 上海 区域",
        metrics=["剩余机柜数"],
        dimensions=["机房"],
        filters=[
            SemanticIntentFilter(
                phrase="上海区域",
                semantic_type="geographic_region",
                expected_column_types=["区域", "gxqy", "region", "area"],
                avoid_column_types=["shipName"],
                relation="parent_region_or_scope",
            )
        ],
        grain="机房",
    )

    details = runner._format_need_analysis_success_details("机房 剩余机柜数 上海 区域")

    assert "问题关键词: 机房 剩余机柜数 上海 区域" in details
    assert "结构化业务意图" in details
    assert "上海区域" in details
    assert "优先绑定字段语义" in details
    assert "gxqy" in details


@pytest.mark.asyncio
async def test_data_agent_runner_system_content_replaces_dataset_menu(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    captured = {}

    async def fake_get_dataset_menu(user_id=None, is_admin=False):
        captured["user_id"] = user_id
        captured["is_admin"] = is_admin
        return "数据集菜单: demo"

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_dataset_menu",
        fake_get_dataset_menu,
    )
    data_config.system_prompt = "Use {dataset_menu}"
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-menu",
        trace_buffer=[],
        user_info={"user_id": 42, "role": "admin"},
    )

    system_content = await runner._build_system_content()

    assert "{dataset_menu}" not in system_content
    assert "数据集菜单: demo" in system_content
    assert captured == {"user_id": 42, "is_admin": True}


@pytest.mark.asyncio
async def test_data_agent_runner_reuses_previous_result_without_native_agent(data_config, monkeypatch):
    from app.core.context import AgentContext, agent_context
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.grounding.ledger import EvidenceLedger
    from app.services.ai.grounding.models import EvidenceType
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：复用上一轮查询结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )
    last_result = {
        "sql": "select status, count(*) total_count from users group by status",
        "dataset_name": "users",
        "data_source": "mysql_oa",
        "rows": [{"status": "启用", "total_count": 8}],
    }

    async def fake_resolve(*args, **kwargs):
        return reuse_turn, None, 12.0

    async def fake_get_last(user_id, conversation_id):
        assert user_id == 42
        assert conversation_id == "conv-1"
        return last_result

    class FakeSynthesisLLM:
        model_name = "synthesis-model"

        async def astream(self, messages):
            assert "上一轮结构化查询结果" in messages[-1].content
            yield AIMessage(content="已基于上一轮结果完成分析。")

    async def forbidden_build_agent(*args, **kwargs):
        raise AssertionError("reuse flow must not build the native AgentScope agent")

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        fake_get_last,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=FakeSynthesisLLM()),
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-reuse",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
    )
    monkeypatch.setattr(runner, "_build_native_agent", forbidden_build_agent)

    ledger = EvidenceLedger(user_id="42", conversation_id="conv-1")
    context_token = agent_context.set(
        AgentContext(
            agent_id="data-agent",
            agent_name="ChatBI",
            user_id=42,
            conversation_id="conv-1",
            grounding_evidence_ledger=ledger,
        )
    )

    try:
        events = []
        async for chunk in runner.execute(
            [
                {"role": "assistant", "content": "上一轮返回了用户状态。"},
                {"role": "user", "content": "分析一下"},
            ]
        ):
            events.append(chunk)
    finally:
        agent_context.reset(context_token)

    assert any(chunk.get("title") == "ChatBI 请求类别分析结果" for chunk in events)
    assert any(chunk.get("title") == "复用上一轮查询结果" for chunk in events)
    assert any(chunk.get("content") == "已基于上一轮结果完成分析。" for chunk in events)
    assert runner.turn_classification is reuse_turn
    assert ledger.has_valid_evidence({EvidenceType.INTERNAL_DATA})
    assert ledger.receipts[0].producer == "chatbi_previous_result"


@pytest.mark.asyncio
async def test_data_agent_runner_does_not_issue_derived_evidence_for_history_only_reuse(
    data_config, monkeypatch
):
    from app.core.context import AgentContext, agent_context
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.grounding.ledger import EvidenceLedger
    from app.services.ai.grounding.models import EvidenceType
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage

    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：只存在历史展示文本",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )

    async def fake_resolve(*args, **kwargs):
        return reuse_turn, None, 0.0

    class FakeSynthesisLLM:
        model_name = "synthesis-model"

        async def astream(self, messages):
            yield AIMessage(content="启用用户数量为 8 人。")

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=FakeSynthesisLLM()),
    )

    ledger = EvidenceLedger(user_id="42", conversation_id="conv-1")
    context_token = agent_context.set(
        AgentContext(
            agent_id="data-agent",
            agent_name="ChatBI",
            user_id=42,
            conversation_id="conv-1",
            grounding_evidence_ledger=ledger,
        )
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-history-only-reuse",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
        debug_options={"grounding_enabled": True},
    )

    events = []
    try:
        async for chunk in runner.execute(
            [
                {
                    "role": "assistant",
                    "content": "上一轮数据结果：| 状态 | 数量 |\n|---|---|\n| 启用 | 8 |",
                },
                {"role": "user", "content": "可视化分析一下"},
            ]
        ):
            events.append(chunk)
    finally:
        agent_context.reset(context_token)

    assert not ledger.has_valid_evidence({EvidenceType.INTERNAL_DATA})
    assert any(
        event.get("grounding_risk", {}).get("level") == "high"
        for event in events
    )


@pytest.mark.asyncio
async def test_data_agent_runner_reuse_synthesis_skips_previous_assistant_history(
    data_config, monkeypatch
):
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage

    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：复用上一轮查询结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )
    last_result = {
        "sql": "select 1",
        "dataset_name": "demo",
        "data_source": "mysql_aiagent",
        "rows": [{"total": 1}],
    }
    captured_messages = []

    async def fake_resolve(*args, **kwargs):
        return reuse_turn, None, 12.0

    async def fake_get_last(user_id, conversation_id):
        return last_result

    class FakeSynthesisLLM:
        model_name = "synthesis-model"

        async def astream(self, messages):
            captured_messages.extend(messages)
            yield AIMessage(content="新增分析。")

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        fake_get_last,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=FakeSynthesisLLM()),
    )

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-reuse-history",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
    )

    async for _chunk in runner.execute(
        [
            {"role": "assistant", "content": "上一轮完整图表与表格内容。"},
            {"role": "user", "content": "可视化分析一下"},
        ]
    ):
        pass

    assert not any(isinstance(message, AIMessage) for message in captured_messages)
    assert any(
        isinstance(message, HumanMessage) and "可视化分析一下" in str(message.content)
        for message in captured_messages
    )


@pytest.mark.asyncio
async def test_data_agent_runner_reuse_synthesis_collapses_duplicated_output(
    data_config, monkeypatch
):
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner
    from app.services.ai.runtime.agentscope.compat import AIMessage

    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：复用上一轮查询结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )
    last_result = {
        "sql": "select 1",
        "dataset_name": "demo",
        "data_source": "mysql_aiagent",
        "rows": [{"total": 1}],
    }
    duplicate_block = "### 核心结论\n" + ("Top5 功能点趋势分析 " * 30)

    async def fake_resolve(*args, **kwargs):
        return reuse_turn, None, 12.0

    async def fake_get_last(user_id, conversation_id):
        return last_result

    class FakeSynthesisLLM:
        model_name = "synthesis-model"

        async def astream(self, messages):
            yield AIMessage(content=duplicate_block + "\n\n" + duplicate_block)

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        fake_get_last,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=FakeSynthesisLLM()),
    )

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-reuse-dedupe",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
        debug_options={"grounding_enabled": True},
    )

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "分析一下"}]):
        events.append(chunk)

    retraction = next(event for event in events if event.get("type") == "retraction")
    assert retraction["content"].strip() == duplicate_block.strip()
    trace_content = runner.trace_buffer[-1].tool_output["content"].strip()
    assert trace_content.startswith(duplicate_block.strip())
    assert "风险提示" in trace_content


@pytest.mark.asyncio
async def test_data_agent_runner_reports_missing_reusable_result(data_config, monkeypatch):
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.executors.prompts import DataQueryPrompts
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    reuse_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        reasoning="测试：复用上一轮查询结果",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )

    async def fake_resolve(*args, **kwargs):
        return reuse_turn, None, 0.0

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        AsyncMock(return_value=None),
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-reuse-miss",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
    )

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "可视化一下"}]):
        events.append(chunk)

    assert any(chunk.get("title") == "缺少可复用查询结果" for chunk in events)
    content_chunks = [chunk.get("content", "") for chunk in events if chunk.get("content")]
    assert any("quick:" in content for content in content_chunks)
    assert any("结构化查询结果" in content for content in content_chunks)


@pytest.mark.asyncio
async def test_data_agent_runner_clarifies_non_data_request_without_native_agent(data_config, monkeypatch):
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    clarify_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.CLARIFICATION_OR_NON_DATA,
        reasoning="用户是在打招呼，不需要查数",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.GENERAL,
    )

    async def fake_resolve(*args, **kwargs):
        return clarify_turn, None, 0.0

    async def forbidden_build_agent(*args, **kwargs):
        raise AssertionError("clarification flow must not build the native AgentScope agent")

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    async def fake_clarification(self, **kwargs):
        return (
            "我可以帮您查询业务数据。\n\n"
            "### 💬 您可以这样继续\n"
            "- [🙋 查询 PUE](quick:查询本月各机房 PUE 趋势)"
        )

    monkeypatch.setattr(
        DataAgentRunner,
        "_generate_clarification_content",
        fake_clarification,
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-clarify",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
    )
    monkeypatch.setattr(runner, "_build_native_agent", forbidden_build_agent)

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "你好，你是谁"}]):
        events.append(chunk)

    assert any(chunk.get("title") == "ChatBI 请求类别分析结果" for chunk in events)
    assert any(chunk.get("title") == "需要补充查数信息" for chunk in events)
    assert any("quick:" in chunk.get("content", "") for chunk in events)


@pytest.mark.asyncio
async def test_generate_clarification_content_returns_string_not_coroutine(data_config):
    import inspect

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-clarify-str", trace_buffer=[])
    content = await runner._generate_clarification_content(
        user_question="你好，你是谁",
        history=[],
        reasoning="当前请求不是明确的 ChatBI 查数请求，需要用户补充想查询的业务数据、指标、维度或时间范围",
    )
    assert not inspect.iscoroutine(content)
    assert isinstance(content, str)
    assert "### ℹ️ 为什么需要补充信息" in content
    assert "(quick:" in content


@pytest.mark.asyncio
async def test_data_agent_runner_clarifies_non_data_with_real_content(data_config, monkeypatch):
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    clarify_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.CLARIFICATION_OR_NON_DATA,
        reasoning="用户是在打招呼，不需要查数",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.GENERAL,
    )

    async def fake_resolve(*args, **kwargs):
        return clarify_turn, None, 0.0

    async def forbidden_build_agent(*args, **kwargs):
        raise AssertionError("clarification flow must not build the native AgentScope agent")

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-clarify-real",
        trace_buffer=[],
    )
    monkeypatch.setattr(runner, "_build_native_agent", forbidden_build_agent)

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "你好，你是谁"}]):
        events.append(chunk)

    content_chunks = [
        chunk.get("content", "")
        for chunk in events
        if isinstance(chunk.get("content"), str) and chunk.get("content")
    ]
    assert content_chunks, "clarification must emit visible content chunk"
    assert any("### ℹ️ 为什么需要补充信息" in content for content in content_chunks)
    assert any("(quick:" in content for content in content_chunks)


@pytest.mark.asyncio
async def test_data_agent_runner_checks_multimodal_compatibility_before_native_model(
    data_config,
    monkeypatch,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_ensure_multimodal_compatible(history, model_name):
        assert history == [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
        assert model_name == "vision-check-model"
        return "当前模型不支持图片输入。"

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_runtime_model_name",
        lambda config, prefer_synthesis=True: "vision-check-model",
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ensure_multimodal_compatible",
        fake_ensure_multimodal_compatible,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        AsyncMock(side_effect=AssertionError("must not initialize native model when multimodal check fails")),
    )
    runner = DataAgentRunner(config=data_config, trace_id="trace-mm", trace_buffer=[])

    events = []
    async for chunk in runner.execute(
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    ):
        events.append(chunk)

    assert events == [{"content": "当前模型不支持图片输入。", "status": "error"}]


@pytest.mark.asyncio
async def test_data_agent_runner_caps_max_steps_at_data_limit(data_config, monkeypatch):
    from app.services.ai.executors.data_executor import DATA_QUERY_MAX_STEPS_CAP
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_get(key):
        assert key == "agent_max_iterations"
        return "99"

    monkeypatch.setattr("app.services.ai.runners.data_agent_runner.ConfigService.get", fake_get)
    runner = DataAgentRunner(config=data_config, trace_id="trace-data", trace_buffer=[])

    assert await runner._resolve_max_steps() == DATA_QUERY_MAX_STEPS_CAP


@pytest.mark.asyncio
async def test_data_agent_runner_builds_native_agent_with_chatbi_toolkit(
    data_config,
    monkeypatch,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class FakeReActConfig:
        def __init__(self, max_iters):
            self.max_iters = max_iters

    async def fake_load_context_config():
        return None

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )

    async def fake_build_model_config(**kwargs):
        return {"fallback_model": None}

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.Agent",
        FakeAgent,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ReActConfig",
        FakeReActConfig,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-data", trace_buffer=[])
    tools = await runner._resolve_runtime_tools_from_config()
    agent = await runner._build_native_agent(
        native_model=object(),
        tools=tools,
        system_content="system",
        max_steps=7,
        primary_model_name="gpt-4o",
    )

    assert isinstance(agent, FakeAgent)
    assert captured["name"] == "DataAgent"
    assert captured["system_prompt"] == "system"
    assert captured["react_config"].max_iters == 7
    schemas = await captured["toolkit"].get_tool_schemas()
    schema_names = [item["function"]["name"] for item in schemas]
    assert schema_names[:3] == [
        "update_dashboard_context",
        "get_dataset_schema",
        "execute_sql_query",
    ]
    assert "get_current_time" in schema_names


@pytest.mark.asyncio
async def test_data_agent_runner_execute_streams_agentscope_native_text(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            assert tools
            tool_names = [tool["function"]["name"] for tool in tools]
            assert tool_names[:3] == [
                "update_dashboard_context",
                "get_dataset_schema",
                "execute_sql_query",
            ]
            assert "get_current_time" in tool_names
            tool_results = [
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            ]
            if not tool_results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "机房"}',
                        )
                    ],
                    is_last=True,
                )
            if len(tool_results) == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT id FROM demo LIMIT 1", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="Data native answer")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-data",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-native-data", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查一下数据"}]):
        events.append(chunk)

    assert "Data native answer" in "".join(
        event["content"] for event in events if "content" in event and "type" not in event
    )
    assert any(event.get("title") == "工具完成: get_dataset_schema" for event in events)
    assert any(event.get("title") == "工具完成: execute_sql_query" for event in events)


@pytest.mark.asyncio
async def test_data_agent_runner_stores_successful_sql_result_for_followups(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            tool_results = [
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            ]
            if not tool_results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "用户状态"}',
                        )
                    ],
                    is_last=True,
                )
            if len(tool_results) == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql",
                            name="execute_sql_query",
                            input=(
                                '{"sql": "SELECT status, count(*) total_count FROM users GROUP BY status", '
                                '"data_source": "mysql_oa", "dataset_name": "users"}'
                            ),
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="查好了")], is_last=True)

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-data",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    async def fake_schema(keywords=None):
        return "table_name: users\ncolumns: status"

    sql_rows = [{"status": "启用", "total_count": 8}]

    async def fake_sql(sql, data_source, dataset_name):
        return sql_rows

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)
    mock_set_last = AsyncMock()
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.set_last_data_result",
        mock_set_last,
    )

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-save-last",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
    )

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "统计用户状态"}]):
        events.append(chunk)

    assert any(chunk.get("content") == "查好了" for chunk in events)
    mock_set_last.assert_awaited_once()
    user_id, conversation_id, payload = mock_set_last.await_args.args
    assert user_id == 42
    assert conversation_id == "conv-1"
    assert payload["sql"] == "SELECT status, count(*) total_count FROM users GROUP BY status"
    assert payload["data_source"] == "mysql_oa"
    assert payload["dataset_name"] == "users"
    assert payload["rows"] == sql_rows
    assert payload["trace_id"] == "trace-save-last"


@pytest.mark.asyncio
async def test_data_agent_runner_injects_few_shot_examples(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    examples = [
        {
            "id": 1001,
            "question": "统计用户状态分布",
            "sql": "SELECT status, count(*) FROM users GROUP BY status",
            "similarity": 0.91,
        }
    ]
    few_shot_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning="测试：需要经验库",
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )

    async def fake_resolve(*args, **kwargs):
        return few_shot_turn, None, 0.0

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            system_text = "\n".join(
                str(getattr(block, "text", ""))
                for msg in messages
                for block in msg.get_content_blocks("text")
            )
            assert system_text.startswith("FEW SHOT SQL EXAMPLES")
            tool_results = [
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            ]
            if not tool_results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "用户状态"}',
                        )
                    ],
                    is_last=True,
                )
            if len(tool_results) == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT status, count(*) FROM users GROUP BY status", "data_source": "mysql_oa", "dataset_name": "users"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="参考案例查好了")], is_last=True)

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-data",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    async def fake_schema(keywords=None):
        return "table_name: users\ncolumns: status"

    async def fake_sql(sql, data_source, dataset_name):
        return [{"status": "启用", "count": 8}]

    from app.services.ai.tools.registry import ToolRegistry

    mock_search = AsyncMock(return_value=examples)
    mock_record = AsyncMock()
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.search_examples",
        mock_search,
    )
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.build_few_shot_prompt",
        lambda matched: "FEW SHOT SQL EXAMPLES",
    )
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.record_usage",
        mock_record,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-few-shot", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "统计用户状态"}]):
        events.append(chunk)

    mock_search.assert_awaited_once()
    assert mock_search.await_args.kwargs["top_k"] is None
    assert any("命中经验库案例" in str(chunk.get("title", "")) for chunk in events)
    mock_record.assert_awaited_once()
    assert any(chunk.get("content") == "参考案例查好了" for chunk in events)


@pytest.mark.asyncio
async def test_data_agent_runner_logs_empty_few_shot_search(
    data_config,
    monkeypatch,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    mock_search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.search_examples",
        mock_search,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-few-shot-empty", trace_buffer=[])
    system_content = await runner._inject_few_shot_examples(
        "SYSTEM",
        user_question="统计用户状态",
        runtime_messages=[],
    )

    assert system_content == "SYSTEM"
    assert runner._fewshot_examples == []
    assert runner._pending_few_shot_log is not None
    assert runner._pending_few_shot_log["title"] == "未命中经验库案例"
    assert "继续基于用户问题和数据集定义生成 SQL" in runner._pending_few_shot_log["details"]
    assert runner.trace_buffer[-1].event_type == "few_shot"
    assert runner.trace_buffer[-1].tool_output == {"examples": []}
    mock_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_data_agent_runner_logs_few_shot_search_failure(
    data_config,
    monkeypatch,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    mock_search = AsyncMock(side_effect=RuntimeError("rag unavailable"))
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.search_examples",
        mock_search,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-few-shot-failure", trace_buffer=[])
    system_content = await runner._inject_few_shot_examples(
        "SYSTEM",
        user_question="统计用户状态",
        runtime_messages=[],
    )

    assert system_content == "SYSTEM"
    assert runner._fewshot_examples == []
    assert runner._pending_few_shot_log is not None
    assert runner._pending_few_shot_log["title"] == "经验库检索不可用"
    assert "已自动跳过案例注入" in runner._pending_few_shot_log["details"]
    assert runner.trace_buffer[-1].event_type == "few_shot"
    assert runner.trace_buffer[-1].raw_log
    assert "rag unavailable" in runner.trace_buffer[-1].raw_log
    mock_search.assert_awaited_once()


@pytest.mark.asyncio
async def test_data_agent_runner_rewrites_contextual_query_and_plans_schema_keywords(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runtime.agentscope.compat import AIMessage
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    new_query_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning="测试：上下文新查数",
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=True,
        intent=IntentType.DATA_QUERY,
    )

    async def fake_resolve(*args, **kwargs):
        return new_query_turn, None, 0.0

    class FakePlannerLLM:
        def __init__(self):
            self.prompts = []

        async def ainvoke(self, messages):
            prompt = messages[0].content
            self.prompts.append(prompt)
            if "查询改写器" in prompt:
                return AIMessage(content="查询上海机房本月 PUE 趋势")
            if "结构化业务意图分析器" in prompt:
                assert "查询上海机房本月 PUE 趋势" in prompt
                return AIMessage(
                    content=(
                        '{"keywords":"上海机房 PUE 本月 趋势 pue_daily room_name",'
                        '"goal":"查询上海机房本月 PUE 趋势",'
                        '"metrics":["PUE"],'
                        '"dimensions":["机房"],'
                        '"filters":[],"time_range":"本月","grain":"日"}'
                    )
                )
            return AIMessage(content="")

    planner = FakePlannerLLM()

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            system_text = "\n".join(
                str(getattr(block, "text", ""))
                for msg in messages
                for block in msg.get_content_blocks("text")
            )
            assert "【Schema 检索词规划】" in system_text
            assert "上海机房 PUE 本月 趋势 pue_daily room_name" in system_text
            assert "查询上海机房本月 PUE 趋势" in system_text
            tool_results = [
                block
                for msg in messages
                for block in msg.get_content_blocks("tool_result")
            ]
            if not tool_results:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "上海机房 PUE 本月 趋势 pue_daily room_name"}',
                        )
                    ],
                    is_last=True,
                )
            if len(tool_results) == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql",
                            name="execute_sql_query",
                            input=(
                                '{"sql": "SELECT day, pue FROM pue_daily WHERE room_name=\'上海机房\' LIMIT 10", '
                                '"data_source": "mysql_oa", "dataset_name": "pue"}'
                            ),
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(content=[TextBlock(text="本月 PUE 查好了")], is_last=True)

    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-data",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        if kwargs.get("streaming") is False:
            return planner
        return handle

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    examples = [
        {
            "id": 2001,
            "question": "查询上海机房 PUE 趋势",
            "dataset_name": "pue",
            "sql": "SELECT day, pue FROM pue_daily WHERE room_name='上海机房'",
            "similarity": 0.88,
        }
    ]

    async def fake_schema(keywords=None):
        assert keywords == "上海机房 PUE 本月 趋势 pue_daily room_name"
        return "table_name: pue_daily\ncolumns: day, pue, room_name"

    async def fake_sql(sql, data_source, dataset_name):
        return {"rows": [{"day": "2026-06-01", "pue": 1.21}], "total": 1}

    from app.services.ai.tools.registry import ToolRegistry

    mock_search = AsyncMock(return_value=examples)
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.config.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.search_examples",
        mock_search,
    )
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.build_few_shot_prompt",
        lambda matched: "FEW SHOT PUE",
    )
    monkeypatch.setattr(
        "app.services.chatbi_example_service.ExampleService.record_usage",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.chatbi.tool_gate_wrapper.detect_time_range_mismatch",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-standalone-plan", trace_buffer=[])

    events = []
    async for chunk in runner.execute(
        [
            {"role": "user", "content": "查询上海机房上周 PUE 趋势"},
            {"role": "assistant", "content": "上海机房上周 PUE 趋势如下。"},
            {"role": "user", "content": "那本月呢"},
        ]
    ):
        events.append(chunk)

    assert mock_search.await_args.args[0] == "查询上海机房本月 PUE 趋势"
    assert any(chunk.get("title") == "用户需求分析" for chunk in events)
    assert any("pue_daily" in chunk.get("details", "") for chunk in events if chunk.get("title") == "用户需求分析")
    assert any(chunk.get("content") == "本月 PUE 查好了" for chunk in events)


@pytest.mark.asyncio
async def test_data_agent_runner_context_action_can_answer_without_sql(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.data_query_turn_classifier import (
        DataQueryTurnClassification,
        DataQueryTurnType,
    )
    from app.services.ai.intent_service import IntentType
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    context_turn = DataQueryTurnClassification(
        turn_type=DataQueryTurnType.CONTEXT_ACTION,
        reasoning="测试：上下文动作",
        requires_fresh_data=False,
        requires_few_shot=False,
        skip_intent_llm=True,
        intent=IntentType.GENERAL,
    )
    last_result = {
        "sql": "select status, count(*) total_count from users group by status",
        "rows": [{"status": "启用", "total_count": 8}],
    }

    async def fake_resolve(*args, **kwargs):
        return context_turn, None, 0.0

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            system_text = "\n".join(
                str(getattr(block, "text", ""))
                for msg in messages
                for block in msg.get_content_blocks("text")
            )
            assert "本轮为上下文动作" in system_text
            assert "可复用的上一轮结构化查询结果" in system_text
            return ChatResponse(content=[TextBlock(text="已基于当前结果记录。")], is_last=True)

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    handle = AgentScopeLLMHandle(
        native_model=FakeModel(
            credential=FakeCredential(),
            model="fake-native-data",
            parameters=FakeModel.Parameters(),
            stream=False,
            max_retries=0,
        ),
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.resolve_data_query_turn_classification",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.ai.memory_service.memory_service.get_last_data_result",
        AsyncMock(return_value=last_result),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-context-action",
        trace_buffer=[],
        user_info={"user_id": 42},
        conversation_id="conv-1",
    )

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "帮我记住这个结果"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert "已基于当前结果记录。" in content
    assert not any(event.get("title") == "阻止未查数回答" for event in events)


@pytest.mark.asyncio
async def test_data_agent_runner_blocks_final_answer_before_required_sql(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="没有查库也能回答")

    runner = DataAgentRunner(config=data_config, trace_id="trace-guard-data", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    assert not any(
        event.get("content") == "没有查库也能回答"
        for event in events
        if isinstance(event, dict)
    )
    assert any(
        "由于未能完成有效的数据检索和计算" in event.get("content", "")
        for event in events
        if isinstance(event, dict)
    )


def test_build_repair_message_when_answer_blocked_without_tools(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-repair-skip", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, blocked_content="没有查库也能回答")
    repair = runner._build_repair_message(state)
    assert "get_dataset_schema" in repair
    assert "execute_sql_query" in repair
    assert runner._build_repair_title(state) == "必须先完成查数流程"


def test_build_repair_message_when_schema_done_sql_missing(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-repair-no-sql", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        blocked_content="直接总结",
    )
    repair = runner._build_repair_message(state)
    assert "execute_sql_query" in repair
    assert "下一步强制动作" in repair
    assert runner._build_repair_title(state) == "必须先执行 SQL 查数"


def test_resolve_repair_tool_choice_forces_sql_after_schema(data_config):
    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-force-sql", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        blocked_content="直接总结",
    )
    choice = runner._resolve_repair_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "execute_sql_query"


def test_resolve_repair_tool_choice_forces_sql_for_empty_result(data_config):
    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-force-sql-empty", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        empty_sql_result=True,
        empty_sql_text="SELECT * FROM demo WHERE status = 'x'",
    )
    choice = runner._resolve_repair_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "execute_sql_query"


def test_resolve_repair_tool_choice_for_tool_loop_fuse(data_config):
    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-fuse-force", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        tool_loop_fuse_triggered=True,
        tool_loop_fuse_reason="重复调用 get_dataset_schema",
    )
    choice = runner._resolve_repair_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "execute_sql_query"


def test_build_repair_message_for_tool_loop_fuse(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-fuse-repair", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        tool_loop_fuse_triggered=True,
        tool_loop_fuse_reason="ping-pong",
    )
    message = runner._build_repair_message(state)
    assert "工具循环熔断" in message
    assert "ping-pong" in message


def test_build_repair_title_for_empty_sql_result(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-title", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        empty_sql_result=True,
    )
    assert runner._build_repair_title(state) == "空结果筛选复核"


def test_preflight_fail_twice_triggers_schema_refresh(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-preflight-refresh", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    sql = "SELECT bad_col FROM demo"
    preflight = "[TOOL_ERROR] SQL 预检失败：未知列 bad_col"

    runner._apply_sql_tool_result(state, tool_args={"sql": sql}, output=preflight)
    assert state.schema_refresh_required is False

    runner._apply_sql_tool_result(state, tool_args={"sql": sql}, output=preflight)
    assert state.schema_refresh_required is True


def test_resolve_repair_tool_choice_forces_sql_for_diagnostic_pending(data_config):
    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-force-sql-diag", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        diagnostic_sql_pending_final=True,
    )
    choice = runner._resolve_repair_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "execute_sql_query"


def test_resolve_initial_tool_choice_forces_sql_after_prefetched_schema(data_config):
    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-initial-force-sql", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        expecting_final_sql_after_diagnostic=True,
    )
    choice = runner._resolve_initial_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "execute_sql_query"


def test_resolve_initial_tool_choice_skips_sql_for_metadata_query(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-metadata-no-force-sql", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        requires_sql_query=False,
        schema_completed=True,
    )

    assert state.ready_to_answer is True
    assert runner._resolve_initial_tool_choice(state) is None


def test_resolve_initial_tool_choice_skips_without_fresh_data_or_schema(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-initial-no-force", trace_buffer=[])

    assert runner._resolve_initial_tool_choice(
        _DataRunState(requires_fresh_data=False, schema_completed=True)
    ) is None
    assert runner._resolve_initial_tool_choice(
        _DataRunState(requires_fresh_data=True, schema_completed=False)
    ) is None
    assert runner._resolve_initial_tool_choice(
        _DataRunState(requires_fresh_data=True, schema_completed=True, sql_completed=True)
    ) is None


def test_resolve_has_data_output_requires_saved_followup_and_visible_content(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-has-data-output", trace_buffer=[])

    runner._last_run_state = _DataRunState(requires_fresh_data=True, followup_data_saved=True, full_content="| a | b |")
    assert runner.resolve_has_data_output() is True

    runner._last_run_state = _DataRunState(requires_fresh_data=True, followup_data_saved=False)
    assert runner.resolve_has_data_output() is False

    runner._last_run_state = _DataRunState(requires_fresh_data=False, followup_data_saved=True, full_content="chart")
    assert runner.resolve_has_data_output() is False


def test_resolve_repair_tool_choice_forces_schema_when_missing(data_config):
    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-force-schema", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, blocked_content="直接回答")
    choice = runner._resolve_repair_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "get_dataset_schema"


def test_build_repair_message_empty_when_no_blocked_content(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-no-repair", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, blocked_content="")
    assert runner._build_repair_message(state) == ""


def test_schema_hit_below_configured_threshold_requests_refinement(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-refine", trace_buffer=[])
    runner._schema_similarity_threshold = 0.55
    state = _DataRunState(requires_fresh_data=True)

    runner._apply_schema_tool_result(
        state,
        "[置信度: 0.54]\n--- Source: unknown.md ---\n字段片段较弱",
    )

    assert state.schema_completed is False
    assert state.schema_needs_refinement is True
    assert state.schema_miss_count == 1
    assert runner._build_repair_title(state) == "优化数据集定义检索"
    assert "相关性不足" in runner._build_repair_message(state)


def test_schema_hit_above_configured_threshold_is_accepted(data_config):
    """Schema 置信度达到 ragflow_similarity_threshold 即可继续查数。"""
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-marginal", trace_buffer=[])
    runner._schema_similarity_threshold = 0.55
    state = _DataRunState(requires_fresh_data=True)

    runner._apply_schema_tool_result(
        state,
        "[置信度: 0.56]\n--- Source: ai_agent_scheduler_jobs.txt ---\n"
        "table_name: ai_agent_scheduler_jobs\n"
        "columns:\n"
        "- name: id\n"
        "- name: job_name\n",
    )

    assert state.schema_completed is True
    assert state.schema_needs_refinement is False
    assert state.schema_miss is False
    assert state.schema_miss_count == 0
    assert state.schema_table_columns["ai_agent_scheduler_jobs"] == ["id", "job_name"]


def test_schema_binding_summary_lists_physical_tables_and_columns(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-binding-summary", trace_buffer=[])
    schema_output = (
        "table_name: HRMRESOURCE\n"
        "columns:\n"
        "- name: SUPDEPID\n"
        "- name: BELONGTO\n"
        "- name: MANAGERID\n"
    )

    summary = runner._build_schema_binding_summary(schema_output)

    assert "Schema Binding 摘要" in summary
    assert "HRMRESOURCE" in summary
    assert "SUPDEPID" in summary
    assert "禁止使用未列出的字段" in summary
    assert "字段后括号内为类型/样例值" in summary


def test_two_schema_misses_or_weak_hits_trigger_fatal(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-fatal-chain", trace_buffer=[])
    runner._schema_similarity_threshold = 0.55
    state = _DataRunState(requires_fresh_data=True)

    runner._apply_schema_tool_result(state, "No relevant schema info found for '机房 列表'.")
    runner._apply_schema_tool_result(
        state,
        "[置信度: 0.54]\n--- Source: ai_agent_scheduler_jobs.txt ---\n表: scheduler",
    )

    assert state.schema_miss_count == 2
    assert runner._is_schema_fatal(state) is True


def test_high_confidence_schema_candidates_require_clarification(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-ambiguous", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True)

    runner._apply_schema_tool_result(
        state,
        "[置信度: 0.88]\n--- Source: access_log.md ---\n数据集: 访问日志\n"
        "\n[置信度: 0.86]\n--- Source: audit_log.md ---\n数据集: 操作审计\n",
    )

    assert state.schema_completed is False
    assert state.schema_ambiguous is True
    assert state.ready_to_answer is True
    assert runner._resolve_initial_tool_choice(state) is None
    assert runner._resolve_repair_tool_choice(state) is None
    assert "多个高置信度" in runner._build_repair_message(state)


def test_schema_success_after_misses_recovers_schema_state(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-recover", trace_buffer=[])
    runner._schema_similarity_threshold = 0.2
    state = _DataRunState(requires_fresh_data=True)

    runner._apply_schema_tool_result(state, "No relevant schema info found for '用户 注册'.")
    runner._apply_schema_tool_result(state, "No relevant schema info found for '用户 注册 数据集'.")
    assert state.schema_miss_count == 2
    assert runner._is_schema_fatal(state) is True

    runner._apply_schema_tool_result(
        state,
        "[置信度: 0.75]\n"
        "--- Source: ai_agent_users.txt ---\n"
        "table_name: ai_agent_users\n"
        "dataset: ai_agent_meta\n"
        "data_source: mysql_aiagent\n"
        "columns:\n"
        "- name: id\n"
        "  type: Int64\n"
        "- name: username\n"
        "  type: String\n",
    )

    assert state.schema_miss is False
    assert state.schema_miss_count == 0
    assert state.schema_completed is True
    assert runner._is_schema_fatal(state) is False
    assert runner._resolve_force_execute_sql_tool_choice(state).mode == "execute_sql_query"


def test_controlled_schema_retry_keywords_recombine_core_business_terms(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-keyword-expand", trace_buffer=[])

    retry_keywords = runner._build_controlled_schema_retry_keywords("所有机房 列表")

    assert retry_keywords.split() == ["机房", "所有机房", "机房列表"]
    assert "数据集" not in retry_keywords
    assert "字段" not in retry_keywords
    assert "物理表" not in retry_keywords
    assert "用户" not in retry_keywords
    assert "角色" not in retry_keywords

    system_retry_keywords = runner._build_controlled_schema_retry_keywords("业务系统 告警")
    assert system_retry_keywords.split() == ["业务系统", "告警", "业务系统告警"]
    assert "数据集" not in system_retry_keywords


def test_controlled_schema_retry_keywords_drop_action_words(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-keyword-actions", trace_buffer=[])

    retry_keywords = runner._build_controlled_schema_retry_keywords("帮我查询昨天用户列表")

    assert "用户" in retry_keywords
    assert "用户列表" in retry_keywords
    assert "帮我" not in retry_keywords
    assert "查询" not in retry_keywords
    assert "昨天" not in retry_keywords


def test_controlled_schema_retry_keywords_keep_original_compound_terms(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-keyword-compound", trace_buffer=[])

    retry_keywords = runner._build_controlled_schema_retry_keywords("PUE统计")

    assert retry_keywords == "PUE统计"


def test_controlled_schema_retry_keywords_are_source_bound(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-keyword-source-bound", trace_buffer=[])

    retry_keywords = runner._build_controlled_schema_retry_keywords("所有机房 列表")

    assert "所有机房" in retry_keywords
    assert "列表" in retry_keywords
    assert "数据集" not in retry_keywords
    assert "物理表" not in retry_keywords
    assert "业务口径" not in retry_keywords
    assert "数据中心" not in retry_keywords
    assert "IDC" not in retry_keywords
    assert "业务系统" not in retry_keywords
    assert "业务系统" in runner._build_controlled_schema_retry_keywords("业务系统 告警")


def test_diagnostic_sql_success_requires_final_sql_before_answer(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-diag-final", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        expecting_final_sql_after_diagnostic=True,
    )
    parsed = {"rows": [{"room_name": "A101"}]}

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT DISTINCT room_name FROM demo LIMIT 10"},
        output=parsed,
    )

    assert state.sql_completed is True
    assert state.diagnostic_sql_pending_final is True
    assert state.expecting_final_sql_after_diagnostic is True
    assert state.ready_to_answer is False
    assert runner._current_repair_kind(state) == "diagnostic_sql_pending_final"
    assert "诊断 SQL" in runner._build_repair_message(state)

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT room_name, count(*) AS total_count FROM demo GROUP BY room_name LIMIT 100"},
        output={"rows": [{"room_name": "A101", "total_count": 3}]},
    )

    assert state.diagnostic_sql_pending_final is False
    assert state.ready_to_answer is True


def test_first_turn_diagnostic_sql_with_data_blocks_answer(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-first-diag", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT DISTINCT months_diff FROM contracts LIMIT 20"},
        output={"rows": [{"months_diff": "0"}, {"months_diff": "3"}]},
    )

    assert should_save is False
    assert state.diagnostic_sql_pending_final is True
    assert state.expecting_final_sql_after_diagnostic is True
    assert state.ready_to_answer is False
    assert runner._current_repair_kind(state) == "diagnostic_sql_pending_final"


def test_aggregate_summary_sql_is_final_business_query(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-agg-summary", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    leave_sql = (
        "SELECT COUNT(*) AS leave_count, SUM(NVL(QJSCT,0)) AS total_days "
        "FROM FORMTABLE_MAIN_11 "
        "WHERE TO_DATE(QJRQQ, 'YYYY-MM-DD') "
        "BETWEEN TO_DATE('2025-07-01', 'YYYY-MM-DD') AND TO_DATE('2026-01-31', 'YYYY-MM-DD')"
    )
    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": leave_sql},
        output={"rows": [{"leave_count": 1727, "total_days": 3122.7}]},
    )

    assert should_save is True
    assert state.diagnostic_sql_pending_final is False
    assert state.ready_to_answer is True


def test_empty_diagnostic_sql_does_not_mark_business_sql_complete(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-diag", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT DISTINCT months_diff FROM contracts LIMIT 20"},
        output={"columns": [{"name": "months_diff"}], "items": []},
    )

    assert should_save is False
    assert state.diagnostic_sql_pending_final is False
    assert state.empty_sql_result is True
    assert state.ready_to_answer is False


def test_final_empty_sql_after_diagnostic_can_answer_no_data(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-final-empty-after-diag", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        expecting_final_sql_after_diagnostic=True,
        diagnostic_sql_pending_final=True,
    )

    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT DATE(created_at) AS reg_date, COUNT(*) AS reg_count FROM users GROUP BY DATE(created_at)"},
        output={"columns": [{"name": "reg_date"}, {"name": "reg_count"}], "items": []},
    )

    assert parsed == {"columns": [{"name": "reg_date"}, {"name": "reg_count"}], "items": []}
    assert should_save is True
    assert state.empty_sql_result is False
    assert state.diagnostic_sql_pending_final is False
    assert state.expecting_final_sql_after_diagnostic is False
    assert state.ready_to_answer is True


def test_detect_empty_result_ignores_total_zero_when_rows_exist(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-total-zero-rows", trace_buffer=[])
    parsed = {
        "rows": [{"region": "华东", "order_cnt": 12840}],
        "total": 0,
    }

    assert runner._detect_empty_result(parsed) is None
    assert runner._result_has_data_rows(parsed) is True


def test_is_diagnostic_sql_does_not_flag_limit_only_queries(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-limit-not-diag", trace_buffer=[])
    assert runner._is_diagnostic_sql("SELECT metric FROM demo LIMIT 10") is False
    assert runner._is_diagnostic_sql("SELECT DISTINCT room_name FROM demo LIMIT 10") is True


def test_final_sql_limit_10_with_data_after_diagnostic_allows_answer(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-final-limit10", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        expecting_final_sql_after_diagnostic=True,
        diagnostic_sql_pending_final=True,
    )

    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT room_name, count(*) AS total_count FROM demo GROUP BY room_name LIMIT 10"},
        output={"rows": [{"room_name": "A101", "total_count": 3}], "total": 0},
    )

    assert should_save is True
    assert parsed["rows"][0]["room_name"] == "A101"
    assert state.empty_sql_result is False
    assert state.diagnostic_sql_pending_final is False
    assert state.expecting_final_sql_after_diagnostic is False
    assert state.ready_to_answer is True


def test_diagnostic_repair_marks_next_sample_shaped_sql_as_final_business_query(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-explicit-final-role", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        expecting_final_sql_after_diagnostic=True,
        diagnostic_sql_pending_final=True,
    )

    runner._reset_state_for_repair(state)
    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={
            "sql": (
                "SELECT id, trace_id, user_id, user_name, feature_name, endpoint, method, "
                "status_code, process_time_ms, client_ip, created_at "
                "FROM api_access_log WHERE created_at IS NOT NULL LIMIT 20"
            )
        },
        output={"items": [{"id": 1, "trace_id": "trace-1"}]},
    )

    assert parsed["items"][0]["id"] == 1
    assert should_save is True
    assert state.diagnostic_sql_pending_final is False
    assert state.expecting_final_sql_after_diagnostic is False
    assert state.ready_to_answer is True


def test_final_business_query_role_survives_duration_anomaly_repair(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-final-role-duration", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        expecting_final_sql_after_diagnostic=True,
        diagnostic_sql_pending_final=True,
    )

    runner._reset_state_for_repair(state)
    _, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={
            "sql": (
                "SELECT id, interval_seconds FROM api_access_log "
                "WHERE interval_seconds IS NOT NULL LIMIT 20"
            )
        },
        output={"items": [{"id": 1, "interval_seconds": -31400679}]},
    )

    assert should_save is False
    assert state.duration_anomaly is True
    runner._reset_state_for_repair(state)

    _, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={
            "sql": (
                "SELECT id, interval_seconds FROM api_access_log "
                "WHERE interval_seconds IS NOT NULL LIMIT 20"
            )
        },
        output={"items": [{"id": 1, "interval_seconds": 315}]},
    )

    assert should_save is True
    assert state.diagnostic_sql_pending_final is False
    assert state.ready_to_answer is True


def test_final_business_query_role_survives_sql_error_repair(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-final-role-sql-error", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        expecting_final_sql_after_diagnostic=True,
        diagnostic_sql_pending_final=True,
    )

    runner._reset_state_for_repair(state)
    _, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={
            "sql": (
                "SELECT id, created_at FROM api_access_log "
                "WHERE created_at IS NOT NULL LIMIT 20"
            )
        },
        output="[TOOL_ERROR] Unknown column 'created_at'",
    )

    assert should_save is False
    assert state.sql_error is True
    runner._reset_state_for_repair(state)

    _, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={
            "sql": (
                "SELECT id, create_time FROM api_access_log "
                "WHERE create_time IS NOT NULL LIMIT 20"
            )
        },
        output={"items": [{"id": 1, "create_time": "2026-07-14 10:00:00"}]},
    )

    assert should_save is True
    assert state.sql_error is False
    assert state.diagnostic_sql_pending_final is False
    assert state.ready_to_answer is True


def test_expecting_final_sql_accepts_non_diagnostic_limit_10_with_data(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-direct-final-limit10", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        expecting_final_sql_after_diagnostic=True,
    )

    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT room_name, count(*) AS total_count FROM demo GROUP BY room_name LIMIT 10"},
        output={"items": [{"room_name": "A101", "total_count": 3}], "total": 0},
    )

    assert should_save is True
    assert state.diagnostic_sql_pending_final is False
    assert state.empty_sql_result is False
    assert state.ready_to_answer is True


@pytest.mark.asyncio
async def test_execute_sql_wrapper_blocks_high_risk_sql_before_tool_call(data_config):
    from app.services.ai.runners.data_agent_runner import (
        DataAgentRunner,
        RuntimeToolSpec,
        SQL_STATIC_GATE_PREFIX,
        _DataRunState,
    )

    called = False

    async def fake_execute(**kwargs):
        nonlocal called
        called = True
        return {"rows": []}

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-static-risk", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    [wrapped] = runner._wrap_tools_with_schema_gate([
        RuntimeToolSpec(
            name="execute_sql_query",
            description="execute",
            parameters_schema={},
            source_type="static",
            callable=fake_execute,
            permission_scope="read",
        )
    ], state)

    output = await wrapped.callable(
        sql="SELECT a.id FROM large_a a JOIN large_b b",
        data_source="mysql_aiagent",
        dataset_name="demo",
    )

    assert called is False
    assert str(output).startswith(SQL_STATIC_GATE_PREFIX)
    assert state.sql_static_risk is True
    assert "JOIN" in state.sql_static_risk_reason


@pytest.mark.asyncio
async def test_execute_sql_wrapper_blocks_time_range_mismatch_before_tool_call(data_config):
    from app.services.ai.time_anchor import build_data_query_time_anchor_block
    from app.services.ai.runners.data_agent_runner import (
        DataAgentRunner,
        RuntimeToolSpec,
        TIME_RANGE_GATE_PREFIX,
        _DataRunState,
    )

    called = False

    async def fake_execute(**kwargs):
        nonlocal called
        called = True
        return {"rows": []}

    runner = DataAgentRunner(config=data_config, trace_id="trace-time-range-gate", trace_buffer=[])
    runner._standalone_query = "帮我查询上个月所有销售人员的拜访记录"
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    [wrapped] = runner._wrap_tools_with_schema_gate([
        RuntimeToolSpec(
            name="execute_sql_query",
            description="execute",
            parameters_schema={},
            source_type="static",
            callable=fake_execute,
            permission_scope="read",
        )
    ], state)

    output = await wrapped.callable(
        sql=(
            "SELECT follow_up_person, visit_date FROM visit_log "
            "WHERE visit_date >= TO_DATE('2025-05-01', 'YYYY-MM-DD') "
            "AND visit_date < TO_DATE('2025-06-01', 'YYYY-MM-DD')"
        ),
        data_source="oracle_crm",
        dataset_name="crm",
    )

    assert called is False
    assert str(output).startswith(TIME_RANGE_GATE_PREFIX)
    assert state.time_range_anomaly is True
    assert "上月" in state.time_range_anomaly_reason
    assert build_data_query_time_anchor_block()  # sanity: anchor module importable in repair path


@pytest.mark.asyncio
async def test_execute_sql_wrapper_blocks_order_by_and_rownum_antipattern(data_config):
    from app.services.ai.runners.data_agent_runner import (
        DataAgentRunner,
        RuntimeToolSpec,
        SQL_STATIC_GATE_PREFIX,
        _DataRunState,
    )

    called = False

    async def fake_execute(**kwargs):
        nonlocal called
        called = True
        return {"rows": []}

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-order-rownum", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    [wrapped] = runner._wrap_tools_with_schema_gate([
        RuntimeToolSpec(
            name="execute_sql_query",
            description="execute",
            parameters_schema={},
            source_type="static",
            callable=fake_execute,
            permission_scope="read",
        )
    ], state)

    output = await wrapped.callable(
        sql=(
            "SELECT opp_code, create_date FROM view_demo "
            "WHERE clue_flag = 0 ORDER BY create_date DESC AND ROWNUM <= 20"
        ),
        data_source="oracle_ds",
        dataset_name="demo",
    )

    assert called is False
    assert str(output).startswith(SQL_STATIC_GATE_PREFIX)
    assert state.sql_static_risk is True
    assert "ORDER BY 后不能接 AND ROWNUM" in state.sql_static_risk_reason


@pytest.mark.asyncio
async def test_execute_sql_wrapper_allows_join_detail_without_limit_before_tool_call(data_config):
    from app.services.ai.runners.data_agent_runner import (
        DataAgentRunner,
        RuntimeToolSpec,
        _DataRunState,
    )

    called = False

    async def fake_execute(**kwargs):
        nonlocal called
        called = True
        return {"rows": []}

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-join-risk", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    [wrapped] = runner._wrap_tools_with_schema_gate([
        RuntimeToolSpec(
            name="execute_sql_query",
            description="execute",
            parameters_schema={},
            source_type="static",
            callable=fake_execute,
            permission_scope="read",
        )
    ], state)

    output = await wrapped.callable(
        sql=(
            "SELECT a.id, b.metric FROM large_a a "
            "JOIN large_b b ON a.id = b.a_id WHERE a.status = 'active'"
        ),
        data_source="mysql_aiagent",
        dataset_name="demo",
    )

    assert called is True
    assert output == {"rows": []}
    assert state.sql_static_risk is False

@pytest.mark.asyncio
async def test_execute_sql_wrapper_allows_lenient_safe_expressions(data_config):
    from app.services.ai.runners.data_agent_runner import (
        DataAgentRunner,
        RuntimeToolSpec,
        _DataRunState,
    )

    called_count = 0

    async def fake_execute(**kwargs):
        nonlocal called_count
        called_count += 1
        return {"rows": [{"id": 1}]}

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-lenient-valid", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    [wrapped] = runner._wrap_tools_with_schema_gate([
        RuntimeToolSpec(
            name="execute_sql_query",
            description="execute",
            parameters_schema={},
            source_type="static",
            callable=fake_execute,
            permission_scope="read",
        )
    ], state)

    # 1. 验证带有头部注释的 SQL 可以放行
    state.sql_static_risk = False
    output1 = await wrapped.callable(
        sql="-- comment: select query\nSELECT id FROM demo LIMIT 1",
        data_source="mysql_aiagent",
        dataset_name="demo",
    )
    assert called_count == 1
    assert state.sql_static_risk is False

    # 2. 验证含 CASE WHEN 复杂排序的 SQL 可以放行
    output2 = await wrapped.callable(
        sql=(
            "SELECT id FROM demo "
            "ORDER BY CASE WHEN status = 'active' AND val > 10 THEN 0 ELSE 1 END LIMIT 10"
        ),
        data_source="mysql_aiagent",
        dataset_name="demo",
    )
    assert called_count == 2
    assert state.sql_static_risk is False

    # 3. 验证 CROSS JOIN 语句可以放行
    output3 = await wrapped.callable(
        sql="SELECT a.id, b.name FROM table_a a CROSS JOIN table_b b LIMIT 10",
        data_source="mysql_aiagent",
        dataset_name="demo",
    )
    assert called_count == 3
    assert state.sql_static_risk is False

    # 4. 验证 USING 语法可以放行
    output4 = await wrapped.callable(
        sql="SELECT id FROM table_a JOIN table_b USING (id) LIMIT 10",
        data_source="mysql_aiagent",
        dataset_name="demo",
    )
    assert called_count == 4
    assert state.sql_static_risk is False

    # 5. 验证 FETCH FIRST 标准分页可以放行
    output5 = await wrapped.callable(
        sql="SELECT id FROM hrmresource FETCH FIRST 10 ROWS ONLY",
        data_source="oracle_ds",
        dataset_name="demo",
    )
    assert called_count == 5
    assert state.sql_static_risk is False

    # 6. 验证 ROWNUM = 1 同样可以放行
    output6 = await wrapped.callable(
        sql="SELECT id FROM hrmresource WHERE accountname = 'chenxiaolong' AND ROWNUM = 1",
        data_source="oracle_ds",
        dataset_name="demo",
    )
    assert called_count == 6
    assert state.sql_static_risk is False



def test_repair_budget_is_tracked_by_error_type(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-repair-budget", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        blocked_content="第一次跳过 SQL",
    )

    assert runner._current_repair_kind(state) == "missing_sql"
    assert runner._repair_budget_exhausted(state) is False
    runner._record_repair_attempt(state)
    assert state.repair_attempts["missing_sql"] == 1
    assert runner._repair_budget_exhausted(state) is False
    runner._record_repair_attempt(state)
    assert state.repair_attempts["missing_sql"] == 2
    assert runner._repair_budget_exhausted(state) is True


@pytest.mark.asyncio
async def test_data_agent_runner_rejects_sql_before_schema(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT 1", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta="[{'one': 1}]")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="结果是 1")

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-before-schema", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    assert not any(event.get("content") == "结果是 1" for event in events if isinstance(event, dict))
    assert any(
        "请先检索数据集定义" in event.get("content", "")
        for event in events
        if isinstance(event, dict)
    )


@pytest.mark.asyncio
async def test_data_agent_runner_schema_gate_blocks_sql_tool(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return [{"ok": 1}]

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    state = _DataRunState(requires_fresh_data=True, schema_completed=False)
    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-gate", trace_buffer=[])
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    result = await wrapped.invoke(
        {"sql": "SELECT id FROM demo LIMIT 1", "data_source": "mysql_aiagent", "dataset_name": "demo"}
    )

    assert invoked is False
    assert "[SCHEMA_GATE]" in str(result)
    state.schema_completed = True
    result2 = await wrapped.invoke(
        {"sql": "SELECT id FROM demo LIMIT 1", "data_source": "mysql_aiagent", "dataset_name": "demo"}
    )
    assert invoked is True
    assert result2 == [{"ok": 1}]


@pytest.mark.asyncio
async def test_data_agent_runner_sql_repeat_gate_blocks_second_sql(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    call_count = 0

    async def fake_sql(**kwargs):
        nonlocal call_count
        call_count += 1
        return '{"columns": [{"name": "id"}], "items": [[1]]}'

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        empty_sql_result=False,
        sql_error=False,
    )
    state.successful_sqls["select id from demo limit 10"] = '{"columns": [{"name": "id"}], "items": [[1]]}'
    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-repeat-gate", trace_buffer=[])
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    result = await wrapped.invoke(
        {"sql": "SELECT id FROM demo LIMIT 10", "data_source": "mysql_aiagent", "dataset_name": "demo"}
    )

    assert call_count == 0
    assert "[SQL_REPEAT_GATE]" in str(result)


@pytest.mark.asyncio
async def test_data_agent_runner_sql_static_gate_takes_precedence_over_repeat_cache(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    call_count = 0

    async def fake_sql(**kwargs):
        nonlocal call_count
        call_count += 1
        return '{"columns": [{"name": "id"}], "items": [[1]]}'

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        empty_sql_result=False,
        sql_error=False,
    )
    risky_sql = "SELECT a.id FROM t1 a JOIN t2 b"
    runner = DataAgentRunner(config=data_config, trace_id="trace-static-before-repeat", trace_buffer=[])
    state.successful_sqls[runner._normalize_sql_text(risky_sql)] = '{"columns": [{"name": "id"}], "items": [[1]]}'
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    result = await wrapped.invoke(
        {"sql": risky_sql, "data_source": "mysql_aiagent", "dataset_name": "demo"}
    )

    assert call_count == 0
    assert "[SQL_STATIC_GATE]" in str(result)
    assert "[SQL_REPEAT_GATE]" not in str(result)
    assert state.sql_static_risk is True
    assert state.sql_repeat_gate_block is False


@pytest.mark.asyncio
async def test_data_agent_runner_sql_repeat_gate_updates_state_completed(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-repeat-state", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=False,
    )

    repeat_gate_output = (
        "[SQL_REPEAT_GATE] 本轮已成功执行过相同的 SQL 查询，禁止重复 execute_sql_query。\n"
        "为保证正常输出，系统已自动为您加载该 SQL 上一次查询成功的缓存数据结果...\n\n"
        '{"columns": [{"name": "id"}], "items": [[1]]}'
    )

    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT 1"},
        output=repeat_gate_output,
    )

    assert state.sql_completed is True
    assert state.sql_repeat_gate_block is True
    assert state.last_successful_sql_output == '{"columns": [{"name": "id"}], "items": [[1]]}'
    assert parsed == {"columns": [{"name": "id"}], "items": [[1]]}
    assert should_save is False


@pytest.mark.asyncio
async def test_data_agent_runner_synthesizes_after_repeated_sql_gate(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id=f"call_sql_{self.calls}",
                        name="execute_sql_query",
                        input='{"sql": "SELECT id FROM demo LIMIT 100", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                    )
                ],
                is_last=True,
            )

    class FakeSynthesisLLM:
        model_name = "synthesis-model"

        async def astream(self, messages):
            assert "重复调用相同 SQL" in messages[-1].content
            yield AIMessage(content="已基于首次 SQL 结果完成回答。")

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "8"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    async def fake_schema(keywords=None):
        return "table_name: demo\ncolumns: id, room, used, total"

    sql_calls = 0

    async def fake_sql(sql, data_source, dataset_name):
        nonlocal sql_calls
        sql_calls += 1
        return {"columns": [{"name": "id"}], "items": [[1]]}

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_synthesis_llm",
        AsyncMock(return_value=FakeSynthesisLLM()),
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-repeat-synth", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查 demo"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert content == "已基于首次 SQL 结果完成回答。"
    assert sql_calls == 1
    assert fake_model.calls < 8
    assert any(event.get("title") == "复用已执行 SQL 结果" for event in events if isinstance(event, dict))


def test_data_agent_runner_tool_loop_fuse_triggers_on_repeated_same_tool_args(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-tool-loop-fuse", trace_buffer=[])
    state = _DataRunState()

    for _ in range(2):
        runner._record_tool_call_signature(state, "get_dataset_schema", {"keywords": "机房"})
        assert state.tool_loop_fuse_triggered is False

    runner._record_tool_call_signature(state, "get_dataset_schema", {"keywords": "机房"})

    assert state.tool_loop_fuse_triggered is True
    assert state.halt_current_react is True
    assert "get_dataset_schema" in state.tool_loop_fuse_reason
    assert runner._current_repair_kind(state) == "tool_loop_fuse"


def test_tool_loop_fuse_does_not_trigger_after_schema_progress(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-progress-fuse", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True)
    tool_args = {"keywords": "用户 注册"}

    for output in (
        "No relevant schema info found for '用户 注册'.",
        "No relevant schema info found for '用户 注册 数据集'.",
    ):
        runner._apply_schema_tool_result(state, output)
        runner._record_tool_call_signature(state, "get_dataset_schema", tool_args)

    runner._apply_schema_tool_result(
        state,
        "[置信度: 0.75]\n"
        "--- Source: ai_agent_users.txt ---\n"
        "table_name: ai_agent_users\n"
        "columns:\n"
        "- name: id\n"
        "  type: Int64\n",
    )
    runner._record_tool_call_signature(state, "get_dataset_schema", tool_args)

    assert state.schema_completed is True
    assert state.tool_loop_fuse_triggered is False
    assert state.tool_call_signatures == {}


def test_data_agent_runner_tool_loop_fuse_triggers_on_ping_pong(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-tool-loop-ping-pong", trace_buffer=[])
    state = _DataRunState()

    for tool_name, tool_args in (
        ("get_dataset_schema", {"keywords": "机房"}),
        ("execute_sql_query", {"sql": "select count(*) from room"}),
        ("get_dataset_schema", {"keywords": "机房 资源"}),
        ("execute_sql_query", {"sql": "select count(*) from room where status = 1"}),
        ("get_dataset_schema", {"keywords": "机房 状态"}),
        ("execute_sql_query", {"sql": "select status, count(*) from room group by status"}),
    ):
        runner._record_tool_call_signature(state, tool_name, tool_args)

    assert state.tool_loop_fuse_triggered is True
    assert state.halt_current_react is True
    assert "交替调用" in state.tool_loop_fuse_reason
    assert runner._current_repair_kind(state) == "tool_loop_fuse"


def test_data_agent_runner_tool_loop_fuse_triggers_on_global_limit(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-tool-loop-global", trace_buffer=[])
    state = _DataRunState()

    for index in range(30):
        runner._record_tool_call_signature(state, f"tool_{index}", {"n": index})

    assert state.tool_loop_fuse_triggered is True
    assert state.halt_current_react is True
    assert "工具调用总数" in state.tool_loop_fuse_reason


@pytest.mark.asyncio
async def test_failed_sql_repeat_gate_blocks_second_identical_attempt(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return "should not run"

    runner = DataAgentRunner(config=data_config, trace_id="trace-failed-sql-repeat", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    sql = "SELECT id FROM demo WHERE bad_col = 1"

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": sql},
        output='[TOOL_ERROR] syntax error near WHERE',
    )
    assert state.failed_sql_signatures[runner._normalize_sql_text(sql)] == 1

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({"sql": sql})
    assert invoked is False
    assert str(result).startswith("[FAILED_SQL_REPEAT_GATE]")
    assert "禁止原样重复" in str(result)


@pytest.mark.asyncio
async def test_sql_preflight_blocks_unknown_alias_column_before_execution(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return "should not run"

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-column", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns: SUPDEPID, BELONGTO, MANAGERID",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": "SELECT r.SUPDEPID, r.SSFB FROM HRMRESOURCE r",
    })

    assert invoked is False
    assert str(result).startswith("[TOOL_ERROR] SQL 预检失败")
    assert '"R"."SSFB": invalid identifier' in str(result)
    assert "HRMRESOURCE" in str(result)
    assert "SUPDEPID" in str(result)

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT r.SUPDEPID, r.SSFB FROM HRMRESOURCE r"},
        output=result,
    )
    assert state.schema_refresh_required is False
    assert runner._current_repair_kind(state) == "sql_error"
    repair = runner._build_repair_message(state)
    assert "SSFB" in repair
    assert "不得继续使用" in repair


@pytest.mark.asyncio
async def test_sql_preflight_blocks_table_not_returned_by_schema_before_execution(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return "should not run"

    async def fake_permission_check(*args, **kwargs):
        return "[Validation Failed] 物理表 'facility_management' 未在元数据中注册或不存在。"

    monkeypatch.setattr(
        "app.services.sql_query_execution_service.check_physical_table_refs_permission",
        fake_permission_check,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-table", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: memory_service_configs\ncolumns: id, service_name, status",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": "SELECT id, name FROM facility_management ORDER BY id",
    })

    assert invoked is False
    assert "facility_management" in str(result)
    assert "未在元数据中注册" in str(result)


@pytest.mark.asyncio
async def test_sql_preflight_allows_table_in_user_permission_set(data_config, monkeypatch):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return '{"columns": [{"name": "id"}], "items": [[1]]}'

    async def fake_permission_check(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.sql_query_execution_service.check_physical_table_refs_permission",
        fake_permission_check,
    )

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-perm-allow", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: view_ai_visit_log\ncolumns: id, follow_up_person",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": (
            "SELECT vl.id, hr.username FROM view_ai_visit_log vl "
            "LEFT JOIN hrmresource hr ON vl.follow_up_person = hr.id"
        ),
        "data_source": "mysql_oa",
    })

    assert invoked is True
    assert str(result).startswith('{"columns"')


@pytest.mark.asyncio
async def test_sql_preflight_allows_known_alias_columns(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return '{"columns": [{"name": "SUPDEPID"}], "items": [[1]]}'

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-allow", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns:\n- name: SUPDEPID\n- name: BELONGTO\n",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": "SELECT r.SUPDEPID FROM HRMRESOURCE r WHERE r.BELONGTO = 1",
    })

    assert invoked is True
    assert str(result).startswith('{"columns"')


@pytest.mark.asyncio
async def test_sql_preflight_allows_cte_names_when_physical_tables_are_in_schema(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return '{"columns": [{"name": "SUPDEPID"}], "items": [[1]]}'

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-cte", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns:\n- name: SUPDEPID\n- name: BELONGTO\n",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": (
            "WITH recent AS (SELECT SUPDEPID, BELONGTO FROM HRMRESOURCE) "
            "SELECT recent.SUPDEPID FROM recent"
        ),
    })

    assert invoked is True
    assert str(result).startswith('{"columns"')


@pytest.mark.asyncio
async def test_sql_preflight_allows_unqualified_columns_for_single_table_to_avoid_dialect_false_positives(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return '{"columns": [{"name": "SUPDEPID"}, {"name": "SSFB"}], "items": [[1, "x"]]}'

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-single-table", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns: SUPDEPID, BELONGTO, MANAGERID",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": "SELECT SUPDEPID, SSFB FROM HRMRESOURCE WHERE BELONGTO = 1",
    })

    assert invoked is True
    assert str(result).startswith('{"columns"')


@pytest.mark.asyncio
async def test_sql_preflight_allows_oracle_rownum_and_select_alias_for_single_table(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return '{"columns": [{"name": "aaa"}], "items": [[1]]}'

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-rownum", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns: SUPDEPID, BELONGTO, MANAGERID",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": "SELECT SUPDEPID AS aaa FROM HRMRESOURCE WHERE ROWNUM <= 10 ORDER BY aaa",
    })

    assert invoked is True
    assert str(result).startswith('{"columns"')


@pytest.mark.asyncio
async def test_sql_preflight_allows_oracle_pseudocolumns_and_no_arg_builtins(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return '{"columns": [{"name": "RID"}], "items": [["AAA"]]}'

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-oracle-builtins", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns: SUPDEPID, BELONGTO, MANAGERID",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({
        "sql": "SELECT ROWID AS rid, SYSDATE AS queried_at FROM HRMRESOURCE WHERE ROWNUM <= 10",
    })

    assert invoked is True
    assert str(result).startswith('{"columns"')


@pytest.mark.asyncio
async def test_sql_preflight_ignores_alias_like_text_inside_literals_and_comments(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked_count = 0

    async def fake_sql(**kwargs):
        nonlocal invoked_count
        invoked_count += 1
        return '{"columns": [{"name": "SUPDEPID"}], "items": [[1]]}'

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-preflight-literals", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    runner._apply_schema_tool_result(
        state,
        "table_name: HRMRESOURCE\ncolumns: SUPDEPID, BELONGTO, MANAGERID, ACCOUNTNAME",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )

    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    sqls = [
        "SELECT r.SUPDEPID FROM HRMRESOURCE r WHERE r.ACCOUNTNAME = 'r.SSFB'",
        "SELECT r.SUPDEPID FROM HRMRESOURCE r -- r.SSFB\nWHERE r.BELONGTO = 1",
        "SELECT r.SUPDEPID FROM HRMRESOURCE r /* r.SSFB */ WHERE r.BELONGTO = 1",
    ]

    for sql in sqls:
        result = await wrapped.invoke({"sql": sql})
        assert str(result).startswith('{"columns"')

    assert invoked_count == len(sqls)


def test_sql_static_risk_ignores_risky_patterns_inside_literals(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    assert DataAgentRunner._detect_sql_static_risk(
        "SELECT name FROM demo WHERE note = 'select * from x'"
    ) == ""
    assert DataAgentRunner._detect_sql_static_risk(
        "SELECT name FROM demo WHERE note = 'ORDER BY x AND ROWNUM <= 1'"
    ) == ""
    assert DataAgentRunner._detect_sql_static_risk("SELECT * FROM demo") == ""
    assert "ORDER BY 后不能接 AND ROWNUM" in DataAgentRunner._detect_sql_static_risk(
        "SELECT id FROM demo ORDER BY created_at DESC AND ROWNUM <= 10"
    )
    assert DataAgentRunner._detect_sql_static_risk(
        "SELECT * FROM (SELECT col1, col2 FROM demo) AS x"
    ) == ""
    assert DataAgentRunner._detect_sql_static_risk(
        "WITH cte AS (SELECT col1, col2 FROM demo) SELECT * FROM cte"
    ) == ""
    assert DataAgentRunner._detect_sql_static_risk(
        "SELECT col1 FROM (SELECT * FROM demo) AS x"
    ) == ""
    assert DataAgentRunner._detect_sql_static_risk(
        "SELECT * FROM (SELECT * FROM demo) AS x"
    ) == ""


def test_schema_reference_sql_error_requires_schema_refresh(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-refresh", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT a FROM t JOIN u ON t.x = u.id"},
        output='[TOOL_ERROR] (1054, "Unknown column \'t.x\' in on clause")',
    )

    assert state.schema_refresh_required is True
    assert state.schema_refreshed_after_sql_error is False
    assert runner._current_repair_kind(state) == "schema_refresh_after_sql_error"
    repair = runner._build_repair_message(state)
    assert "get_dataset_schema" in repair
    assert "禁止原样重复失败 SQL" in repair
    assert runner._is_schema_reference_sql_error(state.last_sql_error_summary)


def test_schema_reference_repair_message_names_invalid_identifier(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-invalid-id", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT r.SSFB FROM HRMRESOURCE r"},
        output='[TOOL_ERROR] 本地执行 SQL 失败，错误信息：ORA-00904: "R"."SSFB": invalid identifier',
    )

    repair = runner._build_repair_message(state)
    assert "无效标识符" in repair
    assert "SSFB" in repair
    assert "不得继续使用" in repair


@pytest.mark.asyncio
async def test_schema_refresh_gate_blocks_sql_until_schema_refetched(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    invoked = False

    async def fake_sql(**kwargs):
        nonlocal invoked
        invoked = True
        return "should not run"

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-refresh-gate", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        schema_refresh_required=True,
        schema_refreshed_after_sql_error=False,
        last_sql_error_summary="unknown column foo",
    )
    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]
    result = await wrapped.invoke({"sql": "SELECT 1"})
    assert invoked is False
    assert str(result).startswith("[SCHEMA_GATE]")
    assert "get_dataset_schema" in str(result)


def test_repair_resets_tool_loop_detector_without_clearing_failed_sql_memory(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-repair-loop-reset", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    state.tool_loop_detector.record("execute_sql_query", {"sql": "SELECT 1"})
    state.tool_loop_detector.record("execute_sql_query", {"sql": "SELECT 1"})
    state.failed_sql_signatures["select 1"] = 1
    state.sql_error = True
    state.last_sql_error_summary = "unknown column x"

    runner._reset_state_for_repair(state)

    assert state.tool_loop_detector.total_calls == 0
    assert state.tool_loop_fuse_triggered is False
    assert state.failed_sql_signatures["select 1"] == 1
    assert state.sql_error is False


def test_failed_sql_repeat_gate_preserves_original_error_summary(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-repeat-preserve-summary", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    original_error = "[TOOL_ERROR] 本地执行 SQL 失败，错误信息：ORA-01861: literal does not match format string"

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT lifecycle_date FROM VIEW_AI_CABINET_BILL_LC"},
        output=original_error,
    )
    repeat_output = (
        "[FAILED_SQL_REPEAT_GATE] 该 SQL 已在上一轮执行失败，禁止原样重复提交。"
        "请根据错误信息修正字段名、表名、JOIN 条件、筛选条件或聚合逻辑后再调用 execute_sql_query。"
        f"\n上次错误摘要：{state.last_sql_error_summary}"
    )

    runner._apply_sql_tool_result(
        state,
        tool_args={"sql": "SELECT lifecycle_date FROM VIEW_AI_CABINET_BILL_LC"},
        output=repeat_output,
    )

    assert state.last_sql_error_summary == original_error
    assert state.sql_error_message == original_error
    assert runner._current_repair_kind(state) == "failed_sql_repeat"
    assert "[FAILED_SQL_REPEAT_GATE]" not in state.last_sql_error_summary
    assert runner._repair_budget_exhausted(state) is False

    runner._record_repair_attempt(state)
    assert runner._repair_budget_exhausted(state) is True


def test_sql_error_repair_message_for_schema_reference_includes_column_guidance(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-error-repair", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        schema_refreshed_after_sql_error=True,
        sql_error=True,
        sql_error_message='unknown column "foo"',
        last_sql_error_summary='unknown column "foo"',
        last_failed_sql_normalized="select foo from bar",
    )

    repair = runner._build_repair_message(state)
    assert "禁止原样重复" in repair
    assert "字段/表引用修正指引" in repair


def test_sql_error_repair_message_for_cross_dataset_scope_guides_federated_path(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-cross-dataset-scope", trace_buffer=[])
    error_message = (
        "[Validation Failed] 表 'HRMRESOURCE' 不属于当前指定的数据集 'meta_yes_crm_ds'，"
        "普通 execute_sql_query 严禁跨数据集或凭空猜表。"
    )
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_error=True,
        sql_error_message=error_message,
        last_sql_error_summary=error_message,
        last_failed_sql_normalized=(
            "select * from view_ai_visit_log v "
            "left join hr_ds.hrmresource r on v.follow_up_person = r.id"
        ),
    )

    repair = runner._build_repair_message(state)

    assert "普通 execute_sql_query 只能查询当前 dataset" in repair
    assert "不要把其他数据集表写进同一条 SQL" in repair
    assert "跨数据集联邦查询流程" in repair
    assert "姓名/部门" in repair


def test_sql_error_repair_message_for_date_format_error_includes_where_probe(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-date-repair", trace_buffer=[])
    failed_sql = (
        "SELECT * FROM VIEW_AI_CULEOPP opp "
        "WHERE opp.CREATE_DATE >= DATE '2026-05-01'"
    )
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_error=True,
        sql_error_message="[TOOL_ERROR] ORA-01861: literal does not match format string",
        last_sql_error_summary="[TOOL_ERROR] ORA-01861: literal does not match format string",
        last_failed_sql_text=failed_sql,
        last_failed_sql_normalized="select * from view_ai_culeopp",
    )

    repair = runner._build_repair_message(state)

    assert "WHERE 条件样例探查" in repair
    assert "ORA-01861" in repair or "01861" in repair
    assert "CREATE_DATE" in repair


def test_failed_sql_repeat_fuses_at_two_after_prior_failure(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-failed-sql-fuse-2", trace_buffer=[])
    state = _DataRunState()
    state.last_sql_error_summary = "unknown column"
    state.failed_sql_signatures["select 1"] = 1
    tool_args = {"sql": "SELECT 1"}

    runner._record_tool_call_signature(state, "execute_sql_query", tool_args)
    assert state.tool_loop_fuse_triggered is False

    runner._record_tool_call_signature(state, "execute_sql_query", tool_args)
    assert state.tool_loop_fuse_triggered is True
    assert "SQL 执行失败" in state.tool_loop_fuse_reason


def test_data_agent_runner_detects_negative_duration_anomaly(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-duration-anomaly", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True, schema_completed=True)
    payload = {
        "columns": [
            {"name": "datacenter_id"},
            {"name": "last_update_time"},
            {"name": "interval_seconds"},
        ],
        "items": [["BJ_YF_01", "2026-06-14T10:00:00", -31400679]],
    }

    parsed, should_save = runner._apply_sql_tool_result(
        state,
        tool_args={
            "sql": "SELECT datacenter_id, last_update_time, interval_seconds FROM demo LIMIT 20",
        },
        output=json.dumps(payload, ensure_ascii=False),
    )

    assert parsed == payload
    assert should_save is False
    assert state.duration_anomaly is True
    assert state.ready_to_answer is False
    assert runner._current_repair_kind(state) == "duration_anomaly"
    assert "interval_seconds" in state.duration_anomaly_reason
    assert "时间差/时延" in runner._build_repair_message(state)


def test_data_agent_runner_detects_extreme_delay_seconds_anomaly(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-delay-anomaly", trace_buffer=[])

    abnormal, reason = runner._detect_duration_anomaly(
        [{"dc": "SH", "latency_seconds": 60 * 60 * 24 * 30}]
    )

    assert abnormal is True
    assert "latency_seconds" in reason


def test_data_agent_runner_allows_normal_duration_values(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-duration-normal", trace_buffer=[])

    abnormal, reason = runner._detect_duration_anomaly(
        {"columns": [{"name": "duration_seconds"}], "items": [[3600]]}
    )

    assert abnormal is False
    assert reason == ""


@pytest.mark.asyncio
async def test_data_agent_runner_sql_repeat_gate_allows_after_empty_result(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    call_count = 0

    async def fake_sql(**kwargs):
        nonlocal call_count
        call_count += 1
        return '{"rows": [], "total": 0}'

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="sql",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        sql_completed=True,
        empty_sql_result=True,
        empty_sql_reason="SQL 返回的行容器为空，未命中任何数据行",
        sql_error=False,
    )
    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-repeat-empty", trace_buffer=[])
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    result = await wrapped.invoke(
        {"sql": "SELECT id FROM demo LIMIT 10", "data_source": "mysql_aiagent", "dataset_name": "demo"}
    )

    assert call_count == 1
    assert "[SQL_REPEAT_GATE]" not in str(result)


@pytest.mark.asyncio
async def test_data_agent_runner_syncs_data_run_state_before_interrupt(data_config, monkeypatch):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    captured_states: list[dict] = []

    async def fake_interrupt(**kwargs):
        captured_states.append(dict(kwargs.get("state") or {}))
        yield {
            "type": kwargs.get("sse_type"),
            "status": "pending",
            "id": "call_perm",
            "permission_request_id": "perm_test",
        }

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.event_stream.stream_pending_tool_interrupt",
        fake_interrupt,
    )

    class FakeAgent:
        class State:
            def model_dump(self, mode="json"):
                return {"context": []}

        state = State()

    async def fake_events():
        yield SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_schema",
            tool_call_name="get_dataset_schema",
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_schema",
            delta='{"dataset": "demo", "columns": ["id"]}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(
            type="REQUIRE_USER_CONFIRM",
            reply_id="reply-1",
            tool_calls=[SimpleNamespace(id="call_perm", name="danger_tool", input="{}")],
        )

    runner = DataAgentRunner(config=data_config, trace_id="trace-pending-sync", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True)

    async for _ in runner._stream_agentscope_events(
        event_stream=fake_events(),
        agent=FakeAgent(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
        state=state,
    ):
        pass

    assert state.schema_completed is True
    assert captured_states
    assert captured_states[0]["data_run_state"]["schema_completed"] is True


def test_data_agent_runner_restores_tool_loop_detector_from_pending_state(data_config):
    from dataclasses import asdict

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector

    original = _DataRunState()
    original.tool_loop_detector.record("get_dataset_schema", {"keywords": "机房"})
    pending_state = {
        "system_content": "system",
        "max_steps": 5,
        "data_run_state": asdict(original),
    }

    restored, stream_meta = DataAgentRunner._pending_state_to_data_run_state(pending_state)

    assert isinstance(restored.tool_loop_detector, ToolLoopDetector)
    assert restored.tool_loop_detector.total_calls == 1
    assert stream_meta == {"system_content": "system", "max_steps": 5}


@pytest.mark.asyncio
async def test_data_agent_runner_repair_after_sql_before_schema(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT 1", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(
            type="TOOL_RESULT_TEXT_DELTA",
            tool_call_id="call_sql",
            delta="[SCHEMA_GATE] 必须先调用 get_dataset_schema",
        )
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-repair", trace_buffer=[])
    state = _DataRunState(requires_fresh_data=True)

    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
        state=state,
    ):
        pass

    repair = runner._build_repair_message(state)
    assert state.sql_before_schema is True
    assert state.sql_completed is False
    assert "Schema 顺序要求" in repair
    assert runner._build_repair_title(state) == "必须先检索数据集定义"


@pytest.mark.asyncio
async def test_data_agent_runner_marks_schema_miss_and_blocks_sql_before_schema(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="No relevant schema info found")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT * FROM demo WHERE id=-1", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta='{"rows": [], "total": 0}')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="没有查到数据")

    runner = DataAgentRunner(config=data_config, trace_id="trace-result-state", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    assert runner._last_run_state.schema_miss is True
    assert runner._last_run_state.sql_before_schema is True
    assert runner._last_run_state.empty_sql_result is False
    assert any("未命中相关数据集定义" in event.get("details", "") for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_marks_no_authorized_schema_and_blocks_sql_before_schema(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="No authorized datasets found")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT bad_col FROM demo LIMIT 10", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta="Unknown column 'bad_col'")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")

    runner = DataAgentRunner(config=data_config, trace_id="trace-error-state", trace_buffer=[])

    async for _ in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        pass

    assert runner._last_run_state.no_authorized_schema is True
    assert runner._last_run_state.schema_completed is False
    assert runner._is_schema_fatal(runner._last_run_state) is True
    assert runner._last_run_state.sql_before_schema is True
    assert runner._last_run_state.sql_error is False


def test_format_tool_details_truncates_long_output(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-trunc", trace_buffer=[])
    details = runner._format_tool_details("execute_sql_query", "x" * 2000, _DataRunState())
    assert details == "x" * 1000 + "\n… [输出已截断]"


def test_format_tool_details_appends_detection_after_truncation(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-trunc-detect", trace_buffer=[])
    state = _DataRunState(empty_sql_reason="SQL 返回的行容器为空，未命中任何数据行")
    tool_args = {"sql": "SELECT 1", "data_source": "mysql_aiagent", "dataset_name": "demo"}
    details = runner._format_tool_details("execute_sql_query", "y" * 2000, state, tool_args)
    assert details.startswith("[Executed SQL]:\nSELECT 1\n\n--- 结果 ---\n")
    assert "y" * 1000 + "\n… [输出已截断]" in details
    assert details.endswith("\n\n[系统检测] SQL 返回的行容器为空，未命中任何数据行")


def test_format_tool_details_includes_sql_on_success(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-prefix", trace_buffer=[])
    payload = json.dumps({"columns": [], "items": [["a"]]}, ensure_ascii=False)
    tool_args = {
        "sql": "SELECT metric FROM demo LIMIT 10",
        "data_source": "clickhouse_ops",
        "dataset_name": "demo",
    }
    details = runner._format_tool_details("execute_sql_query", payload, _DataRunState(), tool_args)
    assert details.startswith("[Executed SQL]:\nSELECT metric FROM demo LIMIT 10\n\n--- 结果 ---\n")
    assert '\n  "items"' in details or '\n  "columns"' in details


def test_format_tool_details_uses_executed_sql_when_row_filter_applied(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-executed-sql", trace_buffer=[])
    payload = json.dumps(
        {
            "columns": [],
            "items": [["a"]],
            "permission_notice": {
                "row_filter_applied": True,
                "dataset_name": "demo",
                "rule_count": 1,
                "message": "已按你的数据权限自动过滤结果",
                "executed_sql": "SELECT metric FROM demo WHERE dept_code = 'D001' LIMIT 10",
            },
        },
        ensure_ascii=False,
    )
    tool_args = {
        "sql": "SELECT metric FROM demo LIMIT 10",
        "data_source": "clickhouse_ops",
        "dataset_name": "demo",
    }
    details = runner._format_tool_details("execute_sql_query", payload, _DataRunState(), tool_args)
    assert details.startswith(
        "[Executed SQL]:\nSELECT metric FROM demo WHERE dept_code = 'D001' LIMIT 10\n\n--- 结果 ---\n"
    )
    assert '"executed_sql"' not in details


def test_format_sql_result_pretty_print_with_row_cap(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-pretty-rows", trace_buffer=[])
    payload = {
        "columns": [{"name": "id", "type": "Int32"}],
        "items": [[i] for i in range(20)],
    }
    text = runner._format_sql_result_for_display(json.dumps(payload, ensure_ascii=False))
    parsed = json.loads(text)
    assert len(parsed["items"]) == 15
    assert parsed["_display_note"] == "仅展示前 15 行，共 20 行"
    assert "\n  " in text


def test_format_tool_details_normalizes_sql_error_output(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-error-fmt", trace_buffer=[])
    output = "[TOOL_ERROR] 本地执行 SQL 失败，错误信息: Unknown column 'bad_col'\n\n[Executed SQL]:\nSELECT bad_col FROM demo"
    state = _DataRunState(sql_error=True, sql_error_message=output[:200])
    details = runner._format_tool_details("execute_sql_query", output, state, {"sql": "SELECT bad_col FROM demo"})
    assert details.startswith("[Executed SQL]:\nSELECT bad_col FROM demo\n\n--- 错误 ---\n")
    assert "Unknown column 'bad_col'" in details
    assert details.count("[Executed SQL]:") == 1


def test_format_tool_details_builds_error_layout_from_plain_text(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-error-plain", trace_buffer=[])
    output = "Unknown column 'bad_col' in 'field list'"
    state = _DataRunState(sql_error=True, sql_error_message=output)
    tool_args = {"sql": "SELECT bad_col FROM demo", "data_source": "mysql", "dataset_name": "demo"}
    details = runner._format_tool_details("execute_sql_query", output, state, tool_args)
    assert details.startswith("[Executed SQL]:\nSELECT bad_col FROM demo\n\n--- 错误 ---\n")
    assert "Unknown column" in details


def test_format_tool_details_skips_sql_prefix_when_already_in_output(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-dup", trace_buffer=[])
    output = "[TOOL_ERROR] timeout\n\n[Executed SQL]:\nSELECT 1"
    state = _DataRunState(sql_error=True, sql_error_message=output)
    tool_args = {"sql": "SELECT 1"}
    details = runner._format_tool_details("execute_sql_query", output, state, tool_args)
    assert details.count("[Executed SQL]:") == 1
    assert details.startswith("[Executed SQL]:\nSELECT 1\n\n--- 错误 ---\n")
    assert "[TOOL_ERROR] timeout" in details


def test_format_tool_details_skips_sql_on_schema_gate(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-gate", trace_buffer=[])
    output = "[SCHEMA_GATE] 必须先调用 get_dataset_schema"
    tool_args = {"sql": "SELECT 1"}
    details = runner._format_tool_details("execute_sql_query", output, _DataRunState(), tool_args)
    assert "[Executed SQL]:" not in details
    assert "[系统检测] 已拦截" in details


def test_detect_sql_error_ignores_failure_keyword_in_result_rows(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-false-positive", trace_buffer=[])
    payload = json.dumps(
        {
            "columns": [{"name": "metric_name", "type": "String"}],
            "items": [["2G12-机组 GB 合闸失败", "2026-06-09T15:35:00", 300, 1752]],
        },
        ensure_ascii=False,
    )
    is_error, message = runner._detect_sql_error(payload)
    assert is_error is False
    assert message == ""


def test_detect_sql_error_still_flags_tool_error_prefix(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(config=data_config, trace_id="trace-tool-error", trace_buffer=[])
    text = "[TOOL_ERROR] 本地执行 SQL 失败，错误信息: timeout\n\n[Executed SQL]:\nSELECT 1"
    is_error, message = runner._detect_sql_error(text)
    assert is_error is True
    assert message.startswith("[TOOL_ERROR]")


@pytest.mark.asyncio
async def test_data_agent_runner_blocks_final_answer_after_sql_error(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: id")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT bad_col FROM demo", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta="Unknown column 'bad_col'")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="查到了")

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-error-block", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    assert not any(event.get("content") == "查到了" for event in events if isinstance(event, dict))
    assert any(
        "数据查询遇到了一些技术问题" in event.get("content", "")
        or "生成的 SQL 存在语法、字段或表引用问题" in event.get("content", "")
        for event in events
        if isinstance(event, dict)
    )


@pytest.mark.asyncio
async def test_data_agent_runner_allows_final_answer_after_trusted_empty_sql_result(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: id")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT * FROM demo WHERE id=-1", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta='{"rows": [], "total": 0}')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="没有数据")

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-block", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    assert any(event.get("content") == "没有数据" for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_blocks_string_filter_empty_sql_result_for_recheck(data_config):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: gxqy, room_name")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta=(
                '{"sql": "SELECT room_name FROM demo WHERE gxqy = \'上海\'", '
                '"data_source": "mysql_aiagent", "dataset_name": "demo"}'
            ),
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta='{"items": [], "total": 0}')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="没有数据")

    runner = DataAgentRunner(config=data_config, trace_id="trace-string-empty-block", trace_buffer=[])

    with patch.object(
        DataAgentRunner,
        "_maybe_run_empty_filter_diagnostics",
        new=AsyncMock(return_value=None),
    ) as mock_diag:
        events = []
        async for chunk in runner._stream_agentscope_events(
            event_stream=fake_events(),
            tools=[],
            native_model=SimpleNamespace(model="fake-native-data"),
            emit_final_guard=False,
        ):
            events.append(chunk)

    assert runner._last_run_state.empty_sql_result is True
    assert not any(event.get("content") == "没有数据" for event in events if isinstance(event, dict))
    mock_diag.assert_awaited_once()


@pytest.mark.asyncio
async def test_data_agent_runner_replaces_generic_failure_reply_for_empty_filter(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-replace", trace_buffer=[])
    state = _DataRunState(
        empty_sql_result=True,
        empty_sql_text="SELECT room_name FROM demo WHERE gxqy = '上海'",
        full_content="数据查询遇到了一些技术问题，暂时无法获取结果。",
        empty_filter_diagnostics=[
            {
                "column": "gxqy",
                "table": "demo",
                "operator": "=",
                "used_values": ["上海"],
                "diagnostic_sql": "SELECT DISTINCT gxqy FROM demo LIMIT 20",
                "candidates": ["上海市"],
                "suggested_values": ["上海市"],
                "error": "",
            }
        ],
    )
    assert runner._should_replace_generic_empty_failure_reply(state) is True


def test_is_trusted_empty_result_rejects_string_literal_filter(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-trusted-empty", trace_buffer=[])
    state = _DataRunState()
    assert (
        runner._is_trusted_empty_result(
            "SELECT room_name FROM demo WHERE region = '上海'",
            state,
        )
        is False
    )
    assert runner._is_trusted_empty_result("SELECT room_name FROM demo WHERE id = 1", state) is True


@pytest.mark.asyncio
async def test_data_agent_runner_blocks_complex_empty_sql_result_for_recheck(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: room, used, total")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT room, SUM(used) / SUM(total) AS utilization_rate FROM demo GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta='{"rows": [], "total": 0}')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="没有数据")

    runner = DataAgentRunner(config=data_config, trace_id="trace-complex-empty-block", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    assert not any(event.get("content") == "没有数据" for event in events if isinstance(event, dict))
    assert any(
        "按当前筛选条件未返回数据" in event.get("content", "")
        for event in events
        if isinstance(event, dict)
    )


@pytest.mark.asyncio
async def test_data_agent_runner_stops_current_react_after_empty_sql_result(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: room, used, total")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql_1", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql_1",
            delta='{"sql": "SELECT room, SUM(used) / SUM(total) AS utilization_rate FROM demo GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql_1", delta='{"rows": [], "total": 0}')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql_1")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql_2", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql_2",
            delta='{"sql": "SELECT room, COUNT(*) FROM demo GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql_2", delta='{"rows": [["A", 1]]}')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql_2")

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-stop-current-react", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
        emit_final_guard=False,
    ):
        events.append(chunk)

    assert runner._last_run_state.empty_sql_result is True
    assert any(event.get("id") == "call_sql_1" for event in events if isinstance(event, dict))
    assert not any(event.get("id") == "call_sql_2" for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_detects_split_sql_plan_before_sql(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: room, used, total")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="<thought><sql_plan>{\"dataset_name\":\"demo\",")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="\"data_source\":\"mysql_aiagent\"}</sql_plan></thought>")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT room, SUM(used) FROM demo GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta='[{"room": "A", "used": 8}]')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", delta="计划后结果是 8")

    runner = DataAgentRunner(config=data_config, trace_id="trace-split-plan", trace_buffer=[])
    state = _DataRunState(requires_sql_plan=True)

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
        state=state,
    ):
        events.append(chunk)

    assert state.sql_plan_seen is True
    assert state.sql_plan_missing is False
    assert any(event.get("content") == "计划后结果是 8" for event in events if isinstance(event, dict))
    assert not any(event.get("title") == "阻止未查数回答" for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_tracks_sql_plan_in_thinking_and_forces_sql(data_config):
    from types import SimpleNamespace

    from agentscope.tool import ToolChoice

    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    async def fake_events():
        yield SimpleNamespace(
            type="THINKING_BLOCK_DELTA",
            delta="<thought><sql_plan>{\"dataset_name\":\"demo\",\"data_source\":\"mysql_aiagent\"}</sql_plan></thought>",
        )

    runner = DataAgentRunner(config=data_config, trace_id="trace-thinking-plan", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_completed=True,
        requires_sql_plan=True,
    )

    async for _chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
        state=state,
        emit_final_guard=False,
    ):
        pass

    assert state.sql_plan_seen is True
    assert state.sql_completed is False
    assert runner._current_repair_kind(state) == "missing_sql"
    choice = runner._resolve_repair_tool_choice(state)
    assert isinstance(choice, ToolChoice)
    assert choice.mode == "execute_sql_query"


@pytest.mark.asyncio
async def test_data_agent_runner_suppresses_duplicate_final_answer_text_blocks(data_config):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: id")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql",
            delta='{"sql": "SELECT id FROM demo", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql", delta='[{"id": 1}]')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql")
        yield SimpleNamespace(type="TEXT_BLOCK_START", block_id="answer-1")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", block_id="answer-1", delta="最终回答")
        yield SimpleNamespace(type="TEXT_BLOCK_END", block_id="answer-1")
        yield SimpleNamespace(type="TEXT_BLOCK_START", block_id="answer-2")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", block_id="answer-2", delta="重复最终回答")
        yield SimpleNamespace(type="TEXT_BLOCK_END", block_id="answer-2")

    runner = DataAgentRunner(config=data_config, trace_id="trace-dup-answer", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    content = "".join(
        event["content"] for event in events if "content" in event and "type" not in event
    )
    assert content == "最终回答"
    assert "重复最终回答" not in content


@pytest.mark.asyncio
async def test_data_agent_runner_allows_final_answer_after_tool_calls_following_partial_text(
    data_config,
):
    from types import SimpleNamespace

    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    async def fake_events():
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_schema", tool_call_name="get_dataset_schema")
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_schema", delta="table_name: demo\ncolumns: id")
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_schema")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql_1", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql_1",
            delta='{"sql": "SELECT role FROM demo", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql_1", delta='[{"role": "admin"}]')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql_1")
        yield SimpleNamespace(type="TEXT_BLOCK_START", block_id="partial-1")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", block_id="partial-1", delta="让我再查询一下按日期分布的注册情况：")
        yield SimpleNamespace(type="TEXT_BLOCK_END", block_id="partial-1")
        yield SimpleNamespace(type="TOOL_CALL_START", tool_call_id="call_sql_2", tool_call_name="execute_sql_query")
        yield SimpleNamespace(
            type="TOOL_CALL_DELTA",
            tool_call_id="call_sql_2",
            delta='{"sql": "SELECT DATE(created_at) AS day FROM demo", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
        )
        yield SimpleNamespace(type="TOOL_RESULT_TEXT_DELTA", tool_call_id="call_sql_2", delta='[{"day": "2026-05-31"}]')
        yield SimpleNamespace(type="TOOL_RESULT_END", tool_call_id="call_sql_2")
        yield SimpleNamespace(type="TEXT_BLOCK_START", block_id="final-1")
        yield SimpleNamespace(type="TEXT_BLOCK_DELTA", block_id="final-1", delta="按日期分布如下：")
        yield SimpleNamespace(type="TEXT_BLOCK_END", block_id="final-1")

    runner = DataAgentRunner(config=data_config, trace_id="trace-partial-then-final", trace_buffer=[])

    events = []
    async for chunk in runner._stream_agentscope_events(
        event_stream=fake_events(),
        tools=[],
        native_model=SimpleNamespace(model="fake-native-data"),
    ):
        events.append(chunk)

    content = "".join(
        event["content"] for event in events if "content" in event and "type" not in event
    )
    assert "让我再查询一下按日期分布的注册情况：" in content
    assert "按日期分布如下：" in content


@pytest.mark.asyncio
async def test_data_agent_runner_execute_repairs_sql_error_before_final_answer(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 2:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_bad_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT bad_col FROM demo LIMIT 10", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 3:
                return ChatResponse(
                    content=[TextBlock(text="错误 SQL 也直接回答")],
                    is_last=True,
                )
            if self.calls == 4:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_fixed_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT id FROM demo LIMIT 100", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="修正后结果是 1")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    async def fake_schema(keywords=None):
        return "table_name: demo\ncolumns: id, bad_col"

    async def fake_sql(sql, data_source, dataset_name):
        if "bad_col" in sql:
            return "SQL syntax error near WHERE"
        return [{"id": 1}]

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-sql-repair", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查 demo"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert "错误 SQL 也直接回答" not in content
    assert "修正后结果是 1" in content
    assert any(event.get("title") == "修正 SQL 查询" for event in events if isinstance(event, dict))
    assert any(event.get("title") == "工具完成: execute_sql_query" for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_double_repair_when_model_skips_sql_twice(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0
            self.tool_choices = []

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            self.tool_choices.append(tool_choice)
            if self.calls == 1:
                return ChatResponse(
                    content=[TextBlock(text="第一次跳过 SQL 直接回答")],
                    is_last=True,
                )
            if self.calls == 2:
                return ChatResponse(
                    content=[TextBlock(text="第一次 repair 仍跳过 SQL")],
                    is_last=True,
                )
            if self.calls == 3:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT id FROM demo LIMIT 100", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="第二次 repair 后查数成功，结果是 1")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    async def fake_schema(keywords=None):
        return "table_name: demo\ncolumns: id, room, used, total"

    async def fake_sql(sql, data_source, dataset_name):
        return [{"id": 1}]

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-double-repair", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查 demo"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert "第一次跳过 SQL 直接回答" not in content
    assert "第一次 repair 仍跳过 SQL" not in content
    assert "第二次 repair 后查数成功，结果是 1" in content
    repair_titles = [
        event.get("title")
        for event in events
        if isinstance(event, dict) and event.get("title") == "必须先执行 SQL 查数"
    ]
    assert len(repair_titles) == 2
    assert fake_model.tool_choices[0] is not None
    assert getattr(fake_model.tool_choices[0], "mode", None) == "execute_sql_query"
    assert fake_model.tool_choices[1] is not None
    assert getattr(fake_model.tool_choices[1], "mode", None) == "execute_sql_query"
    assert fake_model.tool_choices[2] is not None
    assert getattr(fake_model.tool_choices[2], "mode", None) == "execute_sql_query"


@pytest.mark.asyncio
async def test_data_agent_runner_execute_rechecks_empty_sql_before_final_answer(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 2:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_empty_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT room, SUM(used) / SUM(total) AS utilization_rate FROM demo WHERE id=-1 GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 3:
                return ChatResponse(
                    content=[TextBlock(text="直接说没有数据")],
                    is_last=True,
                )
            if self.calls == 4:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_final_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT room, SUM(used) / SUM(total) AS utilization_rate FROM demo WHERE id=1 GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="复查后结果是 1")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    async def fake_schema(keywords=None):
        return "table_name: demo\ncolumns: id, room, used, total"

    async def fake_sql(sql, data_source, dataset_name):
        if "id=-1" in sql:
            return {"rows": [], "total": 0}
        return [{"id": 1}]

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-empty-recheck", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查 demo 明细"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert "直接说没有数据" not in content
    assert "复查后结果是 1" in content
    assert any(event.get("title") == "修正 SQL 查询" for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_execute_continues_repair_when_late_empty_sql_follows_text(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.agent_service import _accumulate_stream_content
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 2:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_simple_empty_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT id FROM demo WHERE id=-1 LIMIT 10", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 3:
                return ChatResponse(
                    content=[
                        TextBlock(text="查询结果为空，让我进一步检查。"),
                        ToolCallBlock(
                            id="call_late_empty_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT room, COUNT(*) AS total_count FROM demo WHERE id=-1 GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        ),
                    ],
                    is_last=True,
                )
            if self.calls == 4:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_final_sql",
                            name="execute_sql_query",
                            input='{"sql": "SELECT room, COUNT(*) AS total_count FROM demo WHERE id=1 GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="复查后结果是 1")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "6"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    async def fake_schema(keywords=None):
        return "table_name: demo\ncolumns: id, room"

    async def fake_sql(sql, data_source, dataset_name):
        if "id=1" in sql:
            return {"rows": [{"room": "A", "total_count": 1}], "total": 1}
        return {"rows": [], "total": 0}

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-late-empty-repair", trace_buffer=[])

    content = ""
    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查 demo 机房列表"}]):
        events.append(chunk)
        content = _accumulate_stream_content(content, chunk)

    assert "查询结果为空，让我进一步检查。" not in content
    assert "复查后结果是 1" in content
    assert any(event.get("type") == "retraction" for event in events if isinstance(event, dict))
    assert any(event.get("id") == "call_final_sql" for event in events if isinstance(event, dict))


@pytest.mark.asyncio
async def test_data_agent_runner_execute_retries_schema_miss_before_sql(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema_miss",
                            name="get_dataset_schema",
                            input='{"keywords": "unknown"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 2:
                return ChatResponse(
                    content=[TextBlock(text="没找到 schema 也回答")],
                    is_last=True,
                )
            if self.calls == 3:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema_retry",
                            name="get_dataset_schema",
                            input='{"keywords": "demo 数据集 表 字段"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 4:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql_after_schema",
                            name="execute_sql_query",
                            input='{"sql": "SELECT id FROM demo LIMIT 100", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="schema 重试后结果是 1")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    schema_calls = 0

    async def fake_schema(keywords=None):
        nonlocal schema_calls
        schema_calls += 1
        if schema_calls == 1:
            return "No relevant schema info found"
        return "table_name: demo\ncolumns: id"

    async def fake_sql(sql, data_source, dataset_name):
        return [{"id": 1}]

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-retry", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "查 demo"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert "没找到 schema 也回答" not in content
    assert "schema 重试后结果是 1" in content
    assert schema_calls == 3
    assert any(event.get("title") == "重试检索数据集定义" for event in events if isinstance(event, dict))


def test_format_tool_details_shows_schema_keywords(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-kw-log", trace_buffer=[])
    state = _DataRunState(last_schema_tool_keywords="机房 列表")
    details = runner._format_tool_details(
        "get_dataset_schema",
        "No relevant schema info found for '机房 列表'.",
        state,
        {"keywords": "机房 列表"},
    )
    assert "[检索关键词] 机房 列表" in details
    assert "[命中摘要]" not in details


def test_format_tool_details_shows_schema_hit_summary(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-summary-log", trace_buffer=[])
    output = (
        "--- [Schema:1] type=table dataset=ai_agent_meta table=ai_agent_access_logs score=0.75 ---\n"
        "table_name: ai_agent_access_logs\n"
        "columns:\n"
        + "  - name: col\n    type: String\n" * 80
    )
    details = runner._format_tool_details(
        "get_dataset_schema",
        output,
        _DataRunState(),
        {"keywords": "AI 代理 访问日志"},
    )
    assert "[检索关键词] AI 代理 访问日志" in details
    assert "[命中摘要] 共命中 1 条元数据记录，占用约" in details
    assert "token" in details
    assert details.index("[命中摘要]") < details.index("--- [Schema:1]")
    assert "… [输出已截断]" in details


@pytest.mark.asyncio
async def test_emit_final_guard_prefers_schema_miss_message(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.executors.prompts import DataQueryPrompts

    runner = DataAgentRunner(config=data_config, trace_id="trace-final-guard-schema", trace_buffer=[])
    state = _DataRunState(
        requires_fresh_data=True,
        schema_miss_count=2,
        blocked_content="这是被拦截的直接回答",
    )

    events = []
    async for chunk in runner._emit_final_guard(state):
        events.append(chunk)

    assert any(event.get("title") == "阻止未查数回答" for event in events if isinstance(event, dict))
    assert any(
        DataQueryPrompts.SCHEMA_MISS_EXHAUSTED_CONTENT in str(event.get("content", ""))
        for event in events
        if event.get("content")
    )


@pytest.mark.asyncio
async def test_emit_final_guard_diagnostic_pending_uses_review_copy_not_no_query_title(
    data_config,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-final-guard-diagnostic",
        trace_buffer=[],
    )
    state = _DataRunState(
        diagnostic_sql_pending_final=True,
        blocked_content="这是基于样本就想下结论的回答",
    )

    events = []
    async for chunk in runner._emit_final_guard(state):
        events.append(chunk)

    log_events = [event for event in events if isinstance(event, dict) and event.get("type") == "log"]
    assert log_events
    assert log_events[0]["title"] == "等待最终查数复核"
    assert "样本" in str(log_events[0].get("details") or "")
    assert "未查数" not in str(log_events[0].get("title") or "")
    assert any(
        "诊断查询已返回样本数据" in str(event.get("content", ""))
        for event in events
        if event.get("content")
    )
    assert not any(event.get("title") == "阻止未查数回答" for event in log_events)


@pytest.mark.asyncio
async def test_data_agent_runner_overrides_schema_retry_with_controlled_keywords(
    data_config,
):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    observed_keywords = []

    async def fake_schema(**kwargs):
        observed_keywords.append(kwargs.get("keywords"))
        return "table_name: room_assets\ncolumns: room_name"

    spec = RuntimeToolSpec(
        name="get_dataset_schema",
        description="schema",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_schema,
        permission_scope="read",
    )
    state = _DataRunState(
        requires_fresh_data=True,
        pending_schema_retry=True,
    )
    state.last_schema_keywords = "所有机房 列表"
    state.controlled_schema_retry_keywords = "机房 所有机房 机房列表"
    runner = DataAgentRunner(config=data_config, trace_id="trace-schema-synonym-retry", trace_buffer=[])
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    await wrapped.invoke({"keywords": "业务系统 告警"})

    assert observed_keywords == ["机房 所有机房 机房列表"]
    assert state.pending_schema_retry is False


@pytest.mark.asyncio
async def test_data_agent_runner_auto_generates_plan_and_executes_high_risk_sql(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    executed_args = []

    async def fake_sql(**kwargs):
        executed_args.append(kwargs)
        return {"columns": [{"name": "room"}, {"name": "ratio"}], "items": [["A", 0.8]]}

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="Execute SQL",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    state = _DataRunState(
        schema_completed=True,
        requires_sql_plan=True,
        sql_plan_seen=False,
        schema_table_columns={"demo": ["room", "used", "total"]},
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-plan-gate",
        trace_buffer=[],
        debug_options={"enable_sql_plan": True},
    )
    runner._standalone_query = "按机房统计利用率"
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    output = await wrapped.invoke(
        {
            "sql": "SELECT room, SUM(used) / SUM(total) AS ratio FROM demo GROUP BY room",
            "data_source": "mysql_aiagent",
            "dataset_name": "demo",
        }
    )

    assert executed_args == [
        {
            "sql": "SELECT room, SUM(used) / SUM(total) AS ratio FROM demo GROUP BY room",
            "data_source": "mysql_aiagent",
            "dataset_name": "demo",
        }
    ]
    assert output == {"columns": [{"name": "room"}, {"name": "ratio"}], "items": [["A", 0.8]]}
    assert state.sql_plan_seen is True
    assert state.sql_plan_missing is False
    assert state.sql_plan_auto_generated is True
    assert state.sql_plan_payload["goal"] == "按机房统计利用率"
    assert state.sql_plan_payload["tables"] == ["demo"]
    assert state.sql_plan_payload["grain"] == "按 room 聚合"
    details = runner._format_tool_details("execute_sql_query", output, state, executed_args[0])
    assert "[平台自动 SQL Plan]" in details
    assert '"goal": "按机房统计利用率"' in details


@pytest.mark.asyncio
async def test_data_agent_runner_refreshes_auto_plan_when_repair_changes_sql(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    async def fake_sql(**kwargs):
        return {"rows": [{"value": 1}]}

    spec = RuntimeToolSpec(
        name="execute_sql_query",
        description="Execute SQL",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=fake_sql,
        permission_scope="read",
    )
    state = _DataRunState(
        schema_completed=True,
        requires_sql_plan=True,
        schema_table_columns={"demo": ["room", "day", "used"]},
    )
    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-plan-refresh",
        trace_buffer=[],
        debug_options={"enable_sql_plan": True},
    )
    wrapped = runner._wrap_tools_with_schema_gate([spec], state)[0]

    first_sql = "SELECT room, SUM(used) FROM demo GROUP BY room"
    second_sql = "SELECT day, SUM(used) FROM demo GROUP BY day"
    await wrapped.invoke({"sql": first_sql, "dataset_name": "demo", "data_source": "mysql"})
    assert state.sql_plan_payload["grain"] == "按 room 聚合"

    await wrapped.invoke({"sql": second_sql, "dataset_name": "demo", "data_source": "mysql"})

    assert state.sql_plan_payload["grain"] == "按 day 聚合"


def test_sql_error_repair_message_includes_error_taxonomy(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner, _DataRunState

    runner = DataAgentRunner(config=data_config, trace_id="trace-taxonomy", trace_buffer=[])
    state = _DataRunState(
        schema_completed=True,
        sql_completed=True,
        sql_error=True,
        sql_error_message='[TOOL_ERROR] ORA-00904: "R"."SSFB": invalid identifier',
        last_sql_error_summary='[TOOL_ERROR] ORA-00904: "R"."SSFB": invalid identifier',
    )

    repair = runner._build_repair_message(state)

    assert "错误分类：invalid_identifier" in repair
    assert "修复重点：核对字段名、表名或别名引用" in repair


@pytest.mark.asyncio
async def test_data_agent_runner_execute_does_not_require_sql_plan_for_high_risk_query(
    data_config,
    monkeypatch,
):
    from agentscope.credential import CredentialBase
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatModelBase, ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    class FakeCredential(CredentialBase):
        @classmethod
        def get_chat_model_class(cls):
            return FakeModel

    class FakeModel(ChatModelBase):
        class Parameters(BaseModel):
            pass

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = 0

        async def _call_api(self, model_name, messages, tools=None, tool_choice=None, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_schema",
                            name="get_dataset_schema",
                            input='{"keywords": "demo 利用率"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 2:
                return ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_sql_without_plan",
                            name="execute_sql_query",
                            input='{"sql": "SELECT room, SUM(used)/SUM(total) AS ratio FROM demo GROUP BY room", "data_source": "mysql_aiagent", "dataset_name": "demo"}',
                        )
                    ],
                    is_last=True,
                )
            if self.calls == 3:
                return ChatResponse(
                    content=[TextBlock(text="无须 SQL 计划也能回答")],
                    is_last=True,
                )
            return ChatResponse(
                content=[TextBlock(text="不应进入 SQL 计划修复")],
                is_last=True,
            )

    async def fake_load_context_config():
        return None

    async def fake_build_model_config(**kwargs):
        return None

    async def fake_config_get(key):
        return "5"

    fake_model = FakeModel(
        credential=FakeCredential(),
        model="fake-native-data",
        parameters=FakeModel.Parameters(),
        stream=False,
        max_retries=0,
    )
    handle = AgentScopeLLMHandle(
        native_model=fake_model,
        model_name="fake-native-data",
        temperature=0.0,
        streaming=True,
    )

    async def fake_get_configured_llm(**kwargs):
        return handle

    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.AgentConfigProvider.get_configured_llm",
        fake_get_configured_llm,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.load_context_config",
        fake_load_context_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.build_model_config",
        fake_build_model_config,
    )
    monkeypatch.setattr(
        "app.services.ai.runners.data_agent_runner.ConfigService.get",
        fake_config_get,
    )

    async def fake_schema(keywords=None):
        return "table_name: demo\ncolumns: room, used, total"

    async def fake_sql(sql, data_source, dataset_name):
        return [{"room": "A", "ratio": 0.8}]

    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", fake_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_sql)

    runner = DataAgentRunner(config=data_config, trace_id="trace-plan-required", trace_buffer=[])

    events = []
    async for chunk in runner.execute([{"role": "user", "content": "按机房统计利用率"}]):
        events.append(chunk)

    content = "".join(event["content"] for event in events if "content" in event and "type" not in event)
    assert "无须 SQL 计划也能回答" in content
    assert "不应进入 SQL 计划修复" not in content
    assert fake_model.calls == 3
    assert not any(event.get("title") == "补充 SQL 计划" for event in events if isinstance(event, dict))


def test_chatbi_grounding_audit_delegates_matching_sql_result(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner
    from app.services.ai.grounding.service import GroundingService

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-chatbi-grounding-match",
        trace_buffer=[],
        user_info={"user_id": "1"},
        conversation_id="conv-1",
    )

    with patch.object(
        GroundingService,
        "audit",
        wraps=GroundingService.audit,
    ) as audit_mock:
        audit = runner._chatbi_grounding_audit(
            candidate_text="王强本月金额为 100 万元。",
            evidence_result={"rows": [{"name": "王强", "amount": 100}]},
        )

    assert audit.should_warn is False
    assert audit_mock.call_count == 1


def test_chatbi_grounding_audit_returns_warning_for_unrelated_business_fact(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-chatbi-grounding-unrelated",
        trace_buffer=[],
        user_info={"user_id": "1"},
        conversation_id="conv-1",
        debug_options={"grounding_enabled": True},
    )

    audit = runner._chatbi_grounding_audit(
        candidate_text="当前销售额排名第一的是王强，金额为 663.98 万元。",
        evidence_result={"rows": [{"name": "李四", "amount": 20}]},
    )

    assert audit.should_warn is True
    assert "风险提示" in audit.warning_chunk["content"]
    assert audit.warning_chunk["grounding_risk"]["level"] == "high"


def test_chatbi_grounding_audit_is_disabled_by_default(data_config):
    from app.services.ai.runners.data_agent_runner import DataAgentRunner

    runner = DataAgentRunner(
        config=data_config,
        trace_id="trace-chatbi-grounding-disabled",
        trace_buffer=[],
        user_info={"user_id": "1"},
        conversation_id="conv-1",
    )

    audit = runner._chatbi_grounding_audit(
        candidate_text="当前销售额排名第一的是王强，金额为 663.98 万元。",
        evidence_result={"rows": [{"name": "李四", "amount": 20}]},
    )

    assert audit.should_warn is False
