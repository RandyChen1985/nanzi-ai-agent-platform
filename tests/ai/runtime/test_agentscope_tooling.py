import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_runtime_tool_spec_executes_callable_and_records_metadata():
    from app.services.ai.runtime.agentscope.tools import RuntimeToolAuditEvent, RuntimeToolSpec

    async def echo_tool(query: str) -> str:
        return f"echo:{query}"

    audit_events: list[RuntimeToolAuditEvent] = []

    spec = RuntimeToolSpec(
        name="echo_tool",
        description="Echo a query",
        parameters_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        source_type="static",
        callable=echo_tool,
        permission_scope="read",
        timeout_seconds=3.0,
        audit_callback=audit_events.append,
    )

    result = await spec.invoke({"query": "hello"})

    assert result == "echo:hello"
    assert spec.is_read_only is True
    assert [event.status for event in audit_events] == ["start", "success"]
    assert audit_events[0].arguments == {"query": "hello"}
    assert audit_events[1].result_preview == "echo:hello"
    assert audit_events[1].elapsed_ms is not None


@pytest.mark.asyncio
async def test_runtime_tool_spec_wraps_timeout_and_emits_error_audit():
    from app.services.ai.runtime.agentscope.errors import RuntimeTimeoutError
    from app.services.ai.runtime.agentscope.tools import RuntimeToolAuditEvent, RuntimeToolSpec

    async def slow_tool() -> str:
        import asyncio

        await asyncio.sleep(0.05)
        return "too late"

    audit_events: list[RuntimeToolAuditEvent] = []
    spec = RuntimeToolSpec(
        name="slow_tool",
        description="Slow tool",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=slow_tool,
        timeout_seconds=0.001,
        audit_callback=audit_events.append,
    )

    with pytest.raises(RuntimeTimeoutError, match="slow_tool"):
        await spec.invoke({})

    assert [event.status for event in audit_events] == ["start", "error"]
    assert audit_events[-1].error


@pytest.mark.asyncio
async def test_runtime_tool_spec_wraps_tool_error():
    from app.services.ai.runtime.agentscope.errors import RuntimeToolError
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    def broken_tool() -> str:
        raise ValueError("bad input")

    spec = RuntimeToolSpec(
        name="broken_tool",
        description="Broken tool",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=broken_tool,
    )

    with pytest.raises(RuntimeToolError, match="bad input") as exc_info:
        await spec.invoke({})

    assert exc_info.value.details["tool_name"] == "broken_tool"


@pytest.mark.asyncio
async def test_agentscope_tool_wrapper_exposes_tool_shape_and_invokes_spec():
    from app.services.ai.runtime.agentscope.tools import (
        AgentScopeRuntimeTool,
        RuntimeToolSpec,
    )

    async def sum_tool(left: int, right: int) -> int:
        return left + right

    spec = RuntimeToolSpec(
        name="sum_tool",
        description="Add two numbers",
        parameters_schema={
            "type": "object",
            "properties": {
                "left": {"type": "integer"},
                "right": {"type": "integer"},
            },
            "required": ["left", "right"],
        },
        source_type="static",
        callable=sum_tool,
        permission_scope="read",
    )

    tool = AgentScopeRuntimeTool(spec)

    assert tool.name == "sum_tool"
    assert tool.description == "Add two numbers"
    assert tool.input_schema["required"] == ["left", "right"]
    assert tool.is_read_only is True
    chunk = await tool(left=1, right=2)
    assert chunk.content[0].text == "3"
    assert chunk.state == "success"


def test_build_toolkit_returns_agent_scope_toolkit_when_available(monkeypatch):
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, build_toolkit

    class FakeToolkit:
        def __init__(self, tools):
            self.tools = tools

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tools._load_agentscope_toolkit",
        lambda: FakeToolkit,
    )

    toolkit = build_toolkit(
        [
            RuntimeToolSpec(
                name="noop",
                description="No-op",
                parameters_schema={"type": "object", "properties": {}},
                source_type="static",
                callable=lambda: "ok",
            )
        ]
    )

    assert isinstance(toolkit, FakeToolkit)
    assert [tool.name for tool in toolkit.tools] == ["noop"]


@pytest.mark.asyncio
async def test_build_toolkit_integrates_with_real_agentscope_toolkit():
    pytest.importorskip("agentscope.tool")

    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, build_toolkit

    toolkit = build_toolkit(
        [
            RuntimeToolSpec(
                name="noop",
                description="No-op",
                parameters_schema={"type": "object", "properties": {}},
                source_type="static",
                callable=lambda: "ok",
                permission_scope="read",
            )
        ]
    )

    schemas = await toolkit.get_tool_schemas()

    assert schemas[0]["function"]["name"] == "noop"
    assert schemas[0]["function"]["description"] == "No-op"
