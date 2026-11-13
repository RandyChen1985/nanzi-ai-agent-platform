"""工具级超时的逐级覆盖：工具级 > 工具自带 > 版本级 > 全局级。

上层是**默认值**而不是天花板：工具级可以把某个慢工具放大到超过上层（配置界面只提示，
运行时不再静默截断）。静默截断会让「界面填的值」和「实际生效的值」对不上，
而那正是本模块要消灭的问题。
"""

import pytest

from app.services.ai.runtime.agentscope.tool_timeout import (
    DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT,
    apply_agent_tool_timeout,
    collect_tool_timeouts,
    resolve_tool_timeout,
)
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec


def _spec(name: str, timeout_seconds: float | None = None) -> RuntimeToolSpec:
    def _call(**kwargs):
        return "ok"

    return RuntimeToolSpec(
        name=name,
        description=name,
        parameters_schema={},
        source_type="static",
        callable=_call,
        timeout_seconds=timeout_seconds,
    )


class TestResolveToolTimeout:
    def test_global_only(self):
        value, source = resolve_tool_timeout(global_timeout=180)
        assert value == 180
        assert source == "global"

    def test_agent_overrides_global(self):
        value, source = resolve_tool_timeout(global_timeout=180, agent_timeout=300)
        assert value == 300
        assert source == "agent"

    def test_tool_tightens_under_agent(self):
        value, source = resolve_tool_timeout(
            tool_config_timeout=60, agent_timeout=300, global_timeout=180
        )
        assert value == 60
        assert source == "tool"

    def test_tool_can_exceed_global_default(self):
        """慢工具可以放大到超过全局默认值：全局 180 时配 600，就按 600 跑。

        这是本轮的语义变更。旧实现会 min 成 180——用户填 600、界面提示按 180 生效，
        而「给某个慢工具更长预算」正是工具级存在的意义。
        """
        value, source = resolve_tool_timeout(
            tool_config_timeout=600, global_timeout=180
        )
        assert value == 600
        assert source == "tool"

    def test_tool_can_exceed_agent_default(self):
        value, source = resolve_tool_timeout(
            tool_config_timeout=600, agent_timeout=300, global_timeout=180
        )
        assert value == 600
        assert source == "tool"

    def test_spec_defined_timeout_is_kept(self):
        """脚本工具自带的超时原样保留，同样不再被上层截断。"""
        value, source = resolve_tool_timeout(
            spec_timeout=90, global_timeout=180
        )
        assert value == 90
        assert source == "tool_defined"

        over, over_source = resolve_tool_timeout(
            spec_timeout=900, global_timeout=180
        )
        assert over == 900
        assert over_source == "tool_defined"

    def test_tool_config_beats_spec_defined(self):
        value, source = resolve_tool_timeout(
            tool_config_timeout=30, spec_timeout=90, global_timeout=180
        )
        assert value == 30
        assert source == "tool"

    @pytest.mark.parametrize("invalid", [0, -5, "abc", None, "", True])
    def test_invalid_tool_value_falls_back(self, invalid):
        value, source = resolve_tool_timeout(
            tool_config_timeout=invalid, global_timeout=180, agent_timeout=300
        )
        assert value == 300
        assert source == "agent"

    def test_all_empty_uses_default(self):
        value, source = resolve_tool_timeout(global_timeout=None)
        assert value == DEFAULT_AGENT_MAX_TOOLCALL_TIMEOUT
        assert source == "global"


class TestApplyAgentToolTimeout:
    def test_batch_keeps_per_tool_values(self):
        """逐工具解析：一个工具单独调，不影响同批其它工具。"""
        specs = [_spec("system_http_request"), _spec("execute_sql_query")]
        applied = apply_agent_tool_timeout(
            specs,
            global_timeout=180,
            tool_timeouts={"system_http_request": 45.0},
        )
        by_name = {spec.name: spec for spec in applied}

        assert by_name["system_http_request"].timeout_seconds == 45
        assert by_name["system_http_request"].timeout_source == "tool"
        assert by_name["execute_sql_query"].timeout_seconds == 180
        assert by_name["execute_sql_query"].timeout_source == "global"

    def test_agent_timeout_applies_to_tools_without_config(self):
        applied = apply_agent_tool_timeout(
            [_spec("execute_sql_query")], global_timeout=180, agent_timeout=300
        )
        assert applied[0].timeout_seconds == 300
        assert applied[0].timeout_source == "agent"

    def test_script_tool_own_timeout_survives(self):
        applied = apply_agent_tool_timeout(
            [_spec("my_script_tool", timeout_seconds=30)], global_timeout=180
        )
        assert applied[0].timeout_seconds == 30
        assert applied[0].timeout_source == "tool_defined"


class TestCollectToolTimeouts:
    def test_collects_from_mixed_shapes(self):
        class _Item:
            def __init__(self, name: str, timeout):
                self.name = name
                self.timeout_seconds = timeout

        collected = collect_tool_timeouts(
            [
                "plain_tool_name",
                {"name": "system_http_request", "timeout_seconds": 600},
                {"name": "no_timeout_tool"},
                {"name": "bad_value_tool", "timeout_seconds": 0},
                _Item("read_image", 120),
            ]
        )

        assert collected == {"system_http_request": 600.0, "read_image": 120.0}


class TestToolTimeoutContextPropagation:
    def test_active_tool_timeout_context_roundtrip(self):
        from app.services.ai.runtime.agentscope.tool_timeout import (
            get_active_tool_timeout_seconds,
            reset_active_tool_timeout_seconds,
            set_active_tool_timeout_seconds,
        )

        assert get_active_tool_timeout_seconds() is None
        tok = set_active_tool_timeout_seconds(900.0)
        try:
            assert get_active_tool_timeout_seconds() == 900.0
        finally:
            reset_active_tool_timeout_seconds(tok)
        assert get_active_tool_timeout_seconds() is None

    @pytest.mark.asyncio
    async def test_resolve_mcp_tool_timeout_honors_active_and_version_over_3600(
        self, monkeypatch
    ):
        from app.core.context import AgentContext, set_agent_context
        from app.services.ai.runtime.agentscope.tool_timeout import (
            reset_active_tool_timeout_seconds,
            set_active_tool_timeout_seconds,
        )
        from app.services.ai.tools.mcp_factory import resolve_mcp_tool_timeout

        set_agent_context(
            AgentContext(
                agent_id="a1",
                agent_name="Main",
                agent_max_toolcall_timeout_seconds=7200.0,
            )
        )
        try:
            # 无 active 时，>3600 且 <=86400 的版本级快照应原样保留
            assert await resolve_mcp_tool_timeout() == 7200.0
            # 有工具级活跃超时时，优先覆盖版本级（包括更大或更小）
            tok = set_active_tool_timeout_seconds(45.0)
            try:
                assert await resolve_mcp_tool_timeout() == 45.0
            finally:
                reset_active_tool_timeout_seconds(tok)
        finally:
            set_agent_context(None)

    @pytest.mark.asyncio
    async def test_resolve_delegation_timeout_honors_active_and_version_over_3600(
        self, monkeypatch
    ):
        from app.core.context import AgentContext, set_agent_context
        from app.services.ai.runtime.agentscope.tool_timeout import (
            reset_active_tool_timeout_seconds,
            set_active_tool_timeout_seconds,
        )
        from app.services.ai.tools import agent_delegate_tool as delegation_tool

        set_agent_context(
            AgentContext(
                agent_id="main-agent",
                agent_name="Main",
                agent_max_toolcall_timeout_seconds=7200.0,
            )
        )
        try:
            # 修复前误用 3600 上限的解析器，会把 7200 截成 180
            assert await delegation_tool._resolve_delegation_timeout_seconds() == 7200.0
            tok = set_active_tool_timeout_seconds(14400.0)
            try:
                assert (
                    await delegation_tool._resolve_delegation_timeout_seconds() == 14400.0
                )
            finally:
                reset_active_tool_timeout_seconds(tok)
        finally:
            set_agent_context(None)

    @pytest.mark.asyncio
    async def test_runtime_tool_stream_propagates_timeout_to_async_generator(self):
        from app.services.ai.runtime.agentscope.tool_timeout import (
            get_active_tool_timeout_seconds,
        )
        from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

        observed: list[float | None] = []

        async def streamed_tool():
            observed.append(get_active_tool_timeout_seconds())
            yield {"status": "ok"}

        spec = RuntimeToolSpec(
            name="mcp_stream_demo",
            description="demo",
            parameters_schema={},
            callable=streamed_tool,
            source_type="mcp",
            timeout_seconds=720.0,
        )
        runtime_tool = spec.to_runtime_tool()
        # 通过 RuntimeTool 实际消费异步生成器期间能读到 720.0，结束后清空
        stream = await runtime_tool()
        collected = [c async for c in stream]
        assert collected
        assert observed == [720.0]
        assert get_active_tool_timeout_seconds() is None
