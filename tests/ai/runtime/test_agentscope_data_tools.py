import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_resolve_chatbi_runtime_tools_uses_defaults_and_required_tools():
    from app.services.ai.runtime.agentscope.data_tools import resolve_chatbi_runtime_tools

    specs = await resolve_chatbi_runtime_tools(None)

    assert [spec.name for spec in specs] == [
        "get_dataset_schema",
        "execute_sql_query",
        "update_dashboard_context",
    ]
    assert [spec.permission_scope for spec in specs] == ["read", "read", "read"]


@pytest.mark.asyncio
async def test_resolve_chatbi_runtime_tools_keeps_config_order_and_appends_required_tools():
    from app.services.ai.runtime.agentscope.data_tools import resolve_chatbi_runtime_tools

    specs = await resolve_chatbi_runtime_tools(["update_dashboard_context"])

    assert [spec.name for spec in specs] == [
        "update_dashboard_context",
        "get_dataset_schema",
        "execute_sql_query",
    ]
    assert [spec.permission_scope for spec in specs] == ["read", "read", "read"]


@pytest.mark.asyncio
async def test_resolve_chatbi_runtime_tools_fails_when_required_tool_is_unavailable(monkeypatch):
    from app.services.ai.runtime.agentscope.data_tools import resolve_chatbi_runtime_tools
    from app.services.ai.runtime.agentscope.errors import RuntimeConfigurationError

    async def fake_get_runtime_tools(tool_names):
        return [spec for spec in []]

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.data_tools.ToolRegistry.get_runtime_tools",
        fake_get_runtime_tools,
    )

    with pytest.raises(RuntimeConfigurationError, match="missing required ChatBI runtime tools"):
        await resolve_chatbi_runtime_tools(["update_dashboard_context"])


@pytest.mark.asyncio
async def test_build_chatbi_toolkit_exposes_agentscope_tool_schemas():
    from app.services.ai.runtime.agentscope.data_tools import build_chatbi_toolkit

    toolkit, specs = await build_chatbi_toolkit(None)
    schemas = await toolkit.get_tool_schemas()

    assert [spec.name for spec in specs] == [
        "get_dataset_schema",
        "execute_sql_query",
        "update_dashboard_context",
    ]
    assert [item["function"]["name"] for item in schemas] == [
        "get_dataset_schema",
        "execute_sql_query",
        "update_dashboard_context",
    ]
    assert schemas[1]["function"]["parameters"]["required"] == [
        "sql",
        "data_source",
        "dataset_name",
    ]
