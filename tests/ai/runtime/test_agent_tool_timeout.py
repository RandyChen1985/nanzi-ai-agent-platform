import asyncio
import inspect
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_agent_tool_timeout_config_parsing_and_precedence():
    from app.services.ai.runtime.agentscope.tool_timeout import (
        DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT,
        apply_agent_tool_timeout,
        effective_tool_timeout,
        parse_agent_max_toolcall_timeout,
        resolve_agent_toolcall_timeout,
    )

    assert DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT == 180.0
    assert parse_agent_max_toolcall_timeout(None) == 180.0
    assert parse_agent_max_toolcall_timeout("3600") == 3600.0
    assert parse_agent_max_toolcall_timeout("3601") == 180.0
    assert parse_agent_max_toolcall_timeout("1.5") == 180.0
    assert effective_tool_timeout(180.0, 30.0, 45.0) == 180.0
    assert effective_tool_timeout(180.0, 300.0, 45.0) == 180.0
    assert effective_tool_timeout(180.0, None, None) == 180.0
    assert resolve_agent_toolcall_timeout(120.0, 300.0) == 300.0
    assert resolve_agent_toolcall_timeout(300.0, 120.0) == 120.0
    assert resolve_agent_toolcall_timeout(45.0, None) == 45.0

    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    specs = [
        RuntimeToolSpec(
            name="long_tool",
            description="",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=lambda: "ok",
        ),
        RuntimeToolSpec(
            name="tool_with_own_timeout",
            description="",
            parameters_schema={"type": "object", "properties": {}},
            source_type="static",
            callable=lambda: "ok",
            timeout_seconds=600.0,
        ),
    ]

    # 上层是默认值，不是天花板：没配的工具继承上层，自带/工具级配置则原样生效
    limited = apply_agent_tool_timeout(specs, 180.0)
    assert [spec.timeout_seconds for spec in limited] == [180.0, 600.0]

    version_limited = apply_agent_tool_timeout(specs, 120.0, agent_timeout=300.0)
    assert [spec.timeout_seconds for spec in version_limited] == [300.0, 600.0]

    version_over_global = apply_agent_tool_timeout(specs, 300.0, agent_timeout=120.0)
    assert [spec.timeout_seconds for spec in version_over_global] == [120.0, 600.0]


@pytest.mark.asyncio
async def test_configured_agent_tool_timeout_reads_one_request_snapshot(monkeypatch):
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_configured_agent_tool_timeout,
    )
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    config_get = AsyncMock(return_value="45")
    monkeypatch.setattr("app.services.config_service.ConfigService.get", config_get)

    spec = RuntimeToolSpec(
        name="snapshot_tool",
        description="",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=lambda: "ok",
    )

    limited = await apply_configured_agent_tool_timeout([spec])

    assert limited[0].timeout_seconds == 45.0
    config_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_configured_agent_tool_timeout_uses_version_override_without_extra_config_reads(monkeypatch):
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_configured_agent_tool_timeout,
    )
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    config_get = AsyncMock(return_value="45")
    monkeypatch.setattr("app.services.config_service.ConfigService.get", config_get)
    # 不带工具自带超时：版本级 30 应当生效，且不额外读系统配置。
    spec = RuntimeToolSpec(
        name="version_snapshot_tool",
        description="",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=lambda: "ok",
    )

    limited = await apply_configured_agent_tool_timeout([spec], agent_timeout=30)

    assert limited[0].timeout_seconds == 30.0
    assert limited[0].timeout_source == "agent"
    config_get.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_defined_timeout_outranks_version_timeout(monkeypatch):
    """脚本工具注册时自带的超时要保留，不能被版本级/全局级整批抹平。"""
    from app.services.ai.runtime.agentscope.tool_timeout import (
        apply_configured_agent_tool_timeout,
    )
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    config_get = AsyncMock(return_value="45")
    monkeypatch.setattr("app.services.config_service.ConfigService.get", config_get)
    spec = RuntimeToolSpec(
        name="script_defined_tool",
        description="",
        parameters_schema={"type": "object", "properties": {}},
        source_type="script_tool",
        callable=lambda: "ok",
        timeout_seconds=10.0,
    )

    limited = await apply_configured_agent_tool_timeout([spec], agent_timeout=30)

    assert limited[0].timeout_seconds == 10.0
    assert limited[0].timeout_source == "tool_defined"


def test_timeout_argument_is_replaced_by_configured_timeout():
    from app.services.ai.runtime.agentscope.tools import _prepare_timeout_arguments

    arguments, timeout_seconds = _prepare_timeout_arguments(
        "http_tool",
        {"timeout": 300},
        {
            "type": "object",
            "properties": {"timeout": {"type": "integer", "description": "Timeout in seconds"}},
        },
        120.0,
    )

    assert timeout_seconds == 120.0
    assert arguments["timeout"] == 120


@pytest.mark.asyncio
async def test_runtime_tool_spec_times_out_sync_callable_without_blocking_event_loop():
    from app.services.ai.runtime.agentscope.errors import RuntimeTimeoutError
    from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

    def slow_sync_tool() -> str:
        time.sleep(0.08)
        return "too late"

    spec = RuntimeToolSpec(
        name="slow_sync_tool",
        description="",
        parameters_schema={"type": "object", "properties": {}},
        source_type="static",
        callable=slow_sync_tool,
        timeout_seconds=0.005,
    )

    with pytest.raises(RuntimeTimeoutError, match="slow_sync_tool"):
        await spec.invoke()


@pytest.mark.asyncio
async def test_legacy_sync_tool_adapter_keeps_timeout_thread_boundary():
    from app.services.ai.runtime.agentscope.errors import RuntimeTimeoutError
    from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool

    class LegacySlowTool:
        name = "legacy_slow_tool"
        description = ""
        input_schema = {"type": "object", "properties": {}}

        def __call__(self, **kwargs):
            time.sleep(0.08)
            return "too late"

    spec = runtime_tool_spec_from_legacy_tool(LegacySlowTool(), source_type="static")
    spec = replace(spec, timeout_seconds=0.005)

    with pytest.raises(RuntimeTimeoutError, match="legacy_slow_tool"):
        await spec.invoke()


@pytest.mark.asyncio
async def test_native_async_generator_uses_total_timeout_and_ignores_shorter_timeout_argument():
    from agentscope.message import TextBlock, ToolResultState
    from agentscope.tool import ToolChunk

    from app.services.ai.runtime.agentscope.tools import (
        RuntimeToolSpec,
        runtime_tool_from_spec,
    )

    class SlowNativeTool:
        name = "Bash"
        description = "Bash"
        input_schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Timeout in milliseconds"},
            },
        }
        is_read_only = False

        def __init__(self) -> None:
            self.received: dict[str, object] = {}

        def __call__(self, **kwargs):
            self.received = dict(kwargs)

            async def stream():
                await asyncio.sleep(0.15)
                yield ToolChunk(
                    content=[TextBlock(text="done")],
                    state=ToolResultState.SUCCESS,
                )

            return stream()

    native = SlowNativeTool()
    audit_events = []
    wrapped = runtime_tool_from_spec(
        RuntimeToolSpec(
            name="Bash",
            description="Bash",
            parameters_schema=native.input_schema,
            source_type="system",
            callable=native,
            permission_scope="ask",
            native_tool=native,
            timeout_seconds=0.1,
            audit_callback=audit_events.append,
        )
    )

    result = await wrapped(command="sleep 1", timeout=1)
    assert inspect.isasyncgen(result)
    chunks = [chunk async for chunk in result]

    assert native.received["timeout"] == 100
    assert chunks[-1].state == ToolResultState.ERROR
    assert "Bash" in chunks[-1].content[0].text
    assert "超时" in chunks[-1].content[0].text or "timed out" in chunks[-1].content[0].text
    assert [event.status for event in audit_events] == ["start", "error"]


@pytest.mark.asyncio
async def test_native_sync_callable_is_also_bounded_by_unified_timeout():
    from agentscope.message import ToolResultState

    from app.services.ai.runtime.agentscope.tools import AgentScopeNativeApprovalTool

    class SlowNativeTool:
        name = "native_sync_slow"
        description = ""
        input_schema = {"type": "object", "properties": {}}
        is_read_only = True

        def __call__(self, **kwargs):
            time.sleep(0.08)
            return "too late"

    result = await AgentScopeNativeApprovalTool(
        SlowNativeTool(),
        timeout_seconds=0.005,
    )()

    assert result.state == ToolResultState.ERROR
    assert "超时" in result.content[0].text
