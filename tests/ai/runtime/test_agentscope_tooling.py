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


@pytest.mark.asyncio
async def test_agentscope_tool_wrapper_maps_runtime_permission_scopes():
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import (
        AgentScopeRuntimeTool,
        RuntimeToolSpec,
    )

    def make_tool(scope: str) -> AgentScopeRuntimeTool:
        return AgentScopeRuntimeTool(
            RuntimeToolSpec(
                name=f"{scope}_tool",
                description="Permission test tool",
                parameters_schema={"type": "object", "properties": {}},
                source_type="static",
                callable=lambda: "ok",
                permission_scope=scope,
            )
        )

    assert (await make_tool("read").check_permissions({}, None)).behavior == PermissionBehavior.ALLOW
    assert (await make_tool("write").check_permissions({}, None)).behavior == PermissionBehavior.ASK
    assert (await make_tool("ask").check_permissions({}, None)).behavior == PermissionBehavior.ASK
    assert (await make_tool("dangerous").check_permissions({}, None)).behavior == PermissionBehavior.DENY

    assert await make_tool("read").check_read_only({}) is True
    assert await make_tool("write").check_read_only({}) is False


@pytest.mark.asyncio
async def test_agentscope_tool_wrapper_honors_runtime_approval_mode_for_non_read_tools():
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import (
        AgentScopeRuntimeTool,
        RuntimeToolSpec,
    )

    spec = RuntimeToolSpec(
        name="write_tool",
        description="Permission test tool",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=lambda: "ok",
        permission_scope="write",
    )

    assert (
        await AgentScopeRuntimeTool(spec, approval_mode="allow").check_permissions({}, None)
    ).behavior == PermissionBehavior.ALLOW
    assert (
        await AgentScopeRuntimeTool(spec, approval_mode="deny").check_permissions({}, None)
    ).behavior == PermissionBehavior.DENY
    assert (
        await AgentScopeRuntimeTool(spec, approval_mode="ask").check_permissions({}, None)
    ).behavior == PermissionBehavior.ASK


@pytest.mark.asyncio
async def test_runtime_approval_mode_does_not_override_read_or_dangerous_tools():
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import (
        AgentScopeRuntimeTool,
        RuntimeToolSpec,
    )

    def make_tool(scope: str) -> AgentScopeRuntimeTool:
        return AgentScopeRuntimeTool(
            RuntimeToolSpec(
                name=f"{scope}_tool",
                description="Permission test tool",
                parameters_schema={"type": "object", "properties": {}},
                source_type="static",
                callable=lambda: "ok",
                permission_scope=scope,
            ),
            approval_mode="allow",
        )

    assert (await make_tool("read").check_permissions({}, None)).behavior == PermissionBehavior.ALLOW
    assert (await make_tool("dangerous").check_permissions({}, None)).behavior == PermissionBehavior.DENY


@pytest.mark.asyncio
async def test_chatbi_runtime_tools_are_allowed_without_ask():
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import AgentScopeRuntimeTool
    from app.services.ai.tools.registry import ToolRegistry

    specs = await ToolRegistry.get_runtime_tools(
        ["get_dataset_schema", "execute_sql_query", "update_dashboard_context"]
    )
    tools = [AgentScopeRuntimeTool(spec) for spec in specs]

    decisions = [await tool.check_permissions({}, None) for tool in tools]

    assert [tool.name for tool in tools] == [
        "get_dataset_schema",
        "execute_sql_query",
        "update_dashboard_context",
    ]
    assert [decision.behavior for decision in decisions] == [
        PermissionBehavior.ALLOW,
        PermissionBehavior.ALLOW,
        PermissionBehavior.ALLOW,
    ]
    assert [await tool.check_read_only({}) for tool in tools] == [True, True, True]


@pytest.mark.asyncio
async def test_chatbi_runtime_tools_ignore_runtime_deny_approval_mode():
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import AgentScopeRuntimeTool
    from app.services.ai.tools.registry import ToolRegistry

    specs = await ToolRegistry.get_runtime_tools(
        ["get_dataset_schema", "execute_sql_query", "update_dashboard_context"]
    )
    tools = [AgentScopeRuntimeTool(spec, approval_mode="deny") for spec in specs]

    decisions = [await tool.check_permissions({}, None) for tool in tools]

    assert [decision.behavior for decision in decisions] == [
        PermissionBehavior.ALLOW,
        PermissionBehavior.ALLOW,
        PermissionBehavior.ALLOW,
    ]


def test_build_toolkit_applies_runtime_approval_mode_to_wrapped_tools(monkeypatch):
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
                name="write_tool",
                description="No-op",
                parameters_schema={"type": "object", "properties": {}},
                source_type="static",
                callable=lambda: "ok",
                permission_scope="write",
            )
        ],
        approval_mode="allow",
    )

    assert toolkit.tools[0].approval_mode == "allow"


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


def test_build_toolkit_wraps_native_agentscope_tools_with_approval_mode(monkeypatch):
    from agentscope.tool import Bash

    from app.services.ai.runtime.agentscope.tools import (
        AgentScopeNativeApprovalTool,
        RuntimeToolSpec,
        build_toolkit,
    )

    class FakeToolkit:
        def __init__(self, tools):
            self.tools = tools

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tools._load_agentscope_toolkit",
        lambda: FakeToolkit,
    )

    native_tool = Bash()
    toolkit = build_toolkit(
        [
            RuntimeToolSpec(
                name="Bash",
                description=native_tool.description,
                parameters_schema=native_tool.input_schema,
                source_type="system",
                callable=lambda: "unused",
                native_tool=native_tool,
            )
        ],
        approval_mode="allow",
    )

    assert isinstance(toolkit.tools[0], AgentScopeNativeApprovalTool)
    assert toolkit.tools[0].native_tool is native_tool
    assert toolkit.tools[0].name == "Bash"
    assert toolkit.tools[0].approval_mode == "allow"


@pytest.mark.asyncio
async def test_native_wrapper_applies_user_forbidden_tool_and_command_checks(monkeypatch):
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from agentscope.tool import Bash

    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    calls = []

    async def deny_forbidden_tool(tool_name, user_id):
        calls.append(("tool", tool_name, user_id))
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="blocked",
            bypass_immune=True,
        )

    async def allow_command(tool_name, tool_input, user_id):
        calls.append(("command", tool_name, user_id, tool_input))
        return None

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tools._enforce_tool_forbidden",
        deny_forbidden_tool,
    )
    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tools._enforce_command_blacklist",
        allow_command,
    )

    tool = AgentScopeNativeApprovalTool(Bash(), approval_mode="allow", user_id=42)
    decision = await tool.check_permissions({"command": "git status"}, None)

    assert decision.behavior == PermissionBehavior.DENY
    assert calls == [("tool", "Bash", 42)]


@pytest.mark.parametrize(
    ("command", "rule", "expected"),
    [
        ("rm -rf /tmp/example", "rm", True),
        ("sudo /bin/rm file.txt", "rm", True),
        ("terraform plan", "rm", False),
        ("echo normal", "rm", False),
        ("shutdown -h now", "shutdown", True),
    ],
)
def test_forbidden_command_matching_uses_shell_tokens(command, rule, expected):
    from app.services.ai.runtime.agentscope.tools import _matches_forbidden_command

    assert _matches_forbidden_command(command, rule) is expected


@pytest.mark.asyncio
async def test_forbidden_tool_check_denies_when_user_policy_cannot_be_loaded(monkeypatch):
    from agentscope.permission import PermissionBehavior

    from app.services.ai.runtime.agentscope.tools import _enforce_tool_forbidden

    class BrokenSessionContext:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(
        "app.core.context.get_current_agent_context",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.core.orm.AsyncSessionLocal",
        lambda: BrokenSessionContext(),
    )

    decision = await _enforce_tool_forbidden("exec_command", 42)

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.decision_reason == "user_permission_policy_unavailable"


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
