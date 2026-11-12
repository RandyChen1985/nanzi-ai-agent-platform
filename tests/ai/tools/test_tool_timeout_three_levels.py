"""工具级超时的三级覆盖：工具级 ≤ 上层（版本级/全局级），只能收紧不能放宽。"""

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

    def test_tool_cannot_exceed_global_ceiling(self):
        """工具级不能顶开系统级：全局 180 时，配 600 也只能按 180 生效。"""
        value, source = resolve_tool_timeout(
            tool_config_timeout=600, global_timeout=180
        )
        assert value == 180
        assert source == "tool"

    def test_tool_cannot_exceed_agent_ceiling(self):
        value, source = resolve_tool_timeout(
            tool_config_timeout=600, agent_timeout=300, global_timeout=180
        )
        assert value == 300
        assert source == "tool"

    def test_spec_defined_timeout_is_kept_but_capped(self):
        """脚本工具自带的超时不再被整批覆盖抹平，但同样不能突破上层。"""
        value, source = resolve_tool_timeout(
            spec_timeout=90, global_timeout=180
        )
        assert value == 90
        assert source == "tool_defined"

        capped, capped_source = resolve_tool_timeout(
            spec_timeout=900, global_timeout=180
        )
        assert capped == 180
        assert capped_source == "tool_defined"

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
        """逐工具解析：一个工具收紧，不影响同批其它工具。"""
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
