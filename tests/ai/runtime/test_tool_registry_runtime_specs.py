import pytest
from unittest.mock import AsyncMock, MagicMock, patch


pytestmark = pytest.mark.no_infrastructure


class FakeArgsSchema:
    @classmethod
    def model_json_schema(cls):
        return {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }


class FakeLegacyTool:
    name = "fake_search"
    description = "Search fake data"
    args_schema = FakeArgsSchema

    async def ainvoke(self, kwargs):
        return f"found:{kwargs['query']}"


class NamedLegacyTool:
    description = "Named legacy tool"

    def __init__(self, name: str):
        self.name = name

    async def ainvoke(self, kwargs):
        return "ok"


class FakeClassTool:
    name = "fake_class_tool"
    description = "Class backed tool"
    input_schema = {
        "type": "object",
        "properties": {"issue_key": {"type": "string"}},
        "required": ["issue_key"],
    }

    async def arun(self, issue_key: str):
        return f"class:{issue_key}"


@pytest.mark.asyncio
async def test_runtime_tool_spec_from_legacy_tool_invokes_ainvoke():
    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(FakeLegacyTool(), source_type="static")

    assert spec.name == "fake_search"
    assert spec.description == "Search fake data"
    assert spec.parameters_schema["required"] == ["query"]
    assert await spec.invoke({"query": "abc"}) == "found:abc"


def test_legacy_runtime_tool_specs_do_not_infer_dangerous_scope():
    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    former_dangerous_names = [
        "execute_sql_query",
        "system_http_request",
        "write_file",
        "exec_command",
        "manage_process",
        "create_skills",
    ]

    specs = [
        runtime_tool_spec_from_legacy_tool(NamedLegacyTool(name), source_type="static")
        for name in former_dangerous_names
    ]

    assert [spec.permission_scope for spec in specs] == ["ask"] * len(former_dangerous_names)


@pytest.mark.asyncio
async def test_runtime_tool_spec_from_class_tool_preserves_class_source_and_schema():
    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    spec = runtime_tool_spec_from_legacy_tool(FakeClassTool(), source_type="class")

    assert spec.source_type == "class"
    assert spec.parameters_schema["required"] == ["issue_key"]
    assert await spec.invoke({"issue_key": "YS-1"}) == "class:YS-1"


@pytest.mark.asyncio
async def test_tool_registry_exposes_runtime_tool_specs(monkeypatch):
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec
    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "fake_search", FakeLegacyTool())

    spec = await ToolRegistry.get_runtime_tool("fake_search")

    assert isinstance(spec, RuntimeToolSpec)
    assert spec.name == "fake_search"
    assert await spec.invoke({"query": "abc"}) == "found:abc"


@pytest.mark.asyncio
async def test_tool_registry_runtime_tools_filters_unknown(monkeypatch):
    from app.services.ai.tools.registry import ToolRegistry

    monkeypatch.setitem(ToolRegistry._registry, "fake_search", FakeLegacyTool())

    specs = await ToolRegistry.get_runtime_tools(["fake_search", "missing_tool"])

    assert [spec.name for spec in specs] == ["fake_search"]


@pytest.mark.asyncio
async def test_tool_registry_maps_legacy_system_tool_names_to_agentscope_builtins():
    from agentscope.tool import Bash, Grep, Read, Write

    from app.services.ai.tools.registry import ToolRegistry

    specs = await ToolRegistry.get_runtime_tools(
        ["exec_command", "read_file", "write_file", "search_text"]
    )

    assert [spec.name for spec in specs] == ["Bash", "Read", "Write", "Grep"]
    assert isinstance(specs[0].native_tool, Bash)
    assert isinstance(specs[1].native_tool, Read)
    assert isinstance(specs[2].native_tool, Write)
    assert isinstance(specs[3].native_tool, Grep)
    assert [spec.permission_scope for spec in specs] == ["ask", "read", "ask", "read"]


@pytest.mark.asyncio
async def test_tool_registry_exposes_configurable_agentscope_edit_and_glob_tools():
    from agentscope.tool import Edit, Glob

    from app.services.ai.tools.registry import ToolRegistry

    specs = await ToolRegistry.get_runtime_tools(["edit_file", "glob_files"])

    assert [spec.name for spec in specs] == ["Edit", "Glob"]
    assert isinstance(specs[0].native_tool, Edit)
    assert isinstance(specs[1].native_tool, Glob)


@pytest.mark.asyncio
async def test_tool_registry_exposes_data_tools_as_read_only_runtime_specs():
    from app.services.ai.tools.registry import ToolRegistry

    specs = await ToolRegistry.get_runtime_tools(
        ["get_dataset_schema", "execute_sql_query", "update_dashboard_context"]
    )

    assert [spec.name for spec in specs] == [
        "get_dataset_schema",
        "execute_sql_query",
        "update_dashboard_context",
    ]
    assert [spec.permission_scope for spec in specs] == ["read", "read", "read"]
    assert specs[0].parameters_schema["properties"]["keywords"]["anyOf"][0]["type"] == "string"
    assert specs[1].parameters_schema["required"] == ["sql", "data_source", "dataset_name"]
    assert "room_name" in specs[2].parameters_schema["properties"]


@pytest.mark.asyncio
async def test_chatbi_runtime_tool_specs_invoke_function_tool_wrappers(monkeypatch):
    from app.services.ai.tools.registry import ToolRegistry
    from app.services.ai.tools.tool_compat import tool

    schema_captured: dict[str, str | None] = {}

    @tool
    async def get_dataset_schema(keywords: str | None = None) -> str:
        schema_captured["keywords"] = keywords
        return "schema-result"

    sql_captured: dict[str, str] = {}

    @tool
    async def execute_sql_query(sql: str, data_source: str, dataset_name: str) -> str:
        sql_captured.update(
            {"sql": sql, "data_source": data_source, "dataset_name": dataset_name}
        )
        return "sql-result"

    monkeypatch.setitem(ToolRegistry._registry, "get_dataset_schema", get_dataset_schema)
    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", execute_sql_query)

    schema_spec = await ToolRegistry.get_runtime_tool("get_dataset_schema")
    sql_spec = await ToolRegistry.get_runtime_tool("execute_sql_query")

    assert await schema_spec.invoke({"keywords": "sales"}) == "schema-result"
    assert schema_captured == {"keywords": "sales"}
    assert await sql_spec.invoke(
        {
            "sql": "SELECT 1",
            "data_source": "mysql_aiagent",
            "dataset_name": "demo",
        }
    ) == "sql-result"
    assert sql_captured == {
        "sql": "SELECT 1",
        "data_source": "mysql_aiagent",
        "dataset_name": "demo",
    }


@pytest.mark.asyncio
async def test_execute_sql_runtime_tool_accepts_legacy_query_alias(monkeypatch):
    from app.services.ai.tools.registry import ToolRegistry

    captured = {}

    async def fake_execute_sql_query(*, sql: str, data_source: str, dataset_name: str):
        captured.update(
            {
                "sql": sql,
                "data_source": data_source,
                "dataset_name": dataset_name,
            }
        )
        return [{"ok": 1}]

    monkeypatch.setitem(ToolRegistry._registry, "execute_sql_query", fake_execute_sql_query)

    spec = await ToolRegistry.get_runtime_tool("execute_sql_query")
    result = await spec.invoke(
        {
            "query": " SELECT 1 ",
            "data_source": "mysql_aiagent",
            "dataset_name": "demo",
        }
    )

    assert result == [{"ok": 1}]
    assert captured == {
        "sql": "SELECT 1",
        "data_source": "mysql_aiagent",
        "dataset_name": "demo",
    }


@pytest.mark.asyncio
async def test_tool_registry_marks_db_generic_api_runtime_tool_source():
    from app.models.tool import SysApiTool
    from app.services.ai.tools.registry import ToolRegistry

    tool_config = SysApiTool(
        name="weather_api",
        description="Weather API",
        method="GET",
        url_template="https://example.test/weather",
        parameter_schema='{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}',
        is_active=True,
    )
    legacy_tool = MagicMock()
    legacy_tool.name = "weather_api"
    legacy_tool.description = "Weather API"
    legacy_tool.args_schema = None
    legacy_tool.ainvoke = AsyncMock(return_value='{"temp": 20}')

    mock_session = AsyncMock()

    def mock_execute_side_effect(stmt):
        result = MagicMock()
        if "mcp" in str(stmt).lower():
            result.scalar_one_or_none.return_value = None
        else:
            result.scalar_one_or_none.return_value = tool_config
        return result

    mock_session.execute.side_effect = mock_execute_side_effect
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("app.services.ai.tools.registry.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.services.ai.tools.registry.GenericApiToolFactory.create_tool", return_value=legacy_tool):
        spec = await ToolRegistry.get_runtime_tool("weather_api")

    assert spec.source_type == "generic_api"
    assert spec.permission_scope == "ask"
    assert await spec.invoke({"city": "Shanghai"}) == '{"temp": 20}'


@pytest.mark.asyncio
async def test_tool_registry_marks_db_mcp_runtime_tool_source():
    from app.models.mcp import McpToolCache
    from app.services.ai.tools.registry import ToolRegistry

    tool_config = McpToolCache(
        id="mcp-tool-id",
        server_id="server-1",
        tool_name="jira:search",
        tool_description="Search Jira",
        parameter_schema='{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}',
        is_published=True,
    )
    legacy_tool = MagicMock()
    legacy_tool.name = "jira:search"
    legacy_tool.description = "Search Jira"
    legacy_tool.args_schema = None
    legacy_tool.ainvoke = AsyncMock(return_value="mcp result")

    mock_session = AsyncMock()

    def mock_execute_side_effect(stmt):
        result = MagicMock()
        if "mcp" in str(stmt).lower():
            result.scalar_one_or_none.return_value = tool_config
        else:
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute.side_effect = mock_execute_side_effect
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__.return_value = mock_session
    mock_session_ctx.__aexit__.return_value = None

    with patch("app.services.ai.tools.registry.AsyncSessionLocal", return_value=mock_session_ctx), \
         patch("app.services.ai.tools.registry.McpToolFactory.create_tool", return_value=legacy_tool):
        spec = await ToolRegistry.get_runtime_tool("jira:search")

    assert spec.source_type == "mcp"
    assert spec.permission_scope == "ask"
    assert await spec.invoke({"query": "project = YS"}) == "mcp result"
