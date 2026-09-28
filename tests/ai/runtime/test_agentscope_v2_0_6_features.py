"""AgentScope 2.0.6 SDK 接入：InjectionConfig / 权限中间件 / structured_output 提取。

这些测试验证「安全挂载」：默认不改变 HITL 与工具权限判定结果。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentscope.permission import PermissionBehavior, PermissionDecision

from app.services.ai.runtime.agentscope.messages import extract_msg_structured_output
from app.services.ai.runtime.agentscope.middleware import ToolPermissionMiddleware


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_load_injection_config_binds_platform_timezone(monkeypatch):
    from app.services.ai.runtime.agentscope import agent_runtime as ar

    monkeypatch.setattr(
        "app.services.platform_timezone.get_cached_platform_timezone",
        lambda: "Asia/Shanghai",
    )

    async def _fake_get(key: str):
        return None

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        staticmethod(_fake_get),
    )

    config = await ar.load_injection_config()
    assert config.inject_runtime_state is True
    assert config.timezone == "Asia/Shanghai"
    assert config.time_interval == 0.5

    config_off = await ar.load_injection_config(inject_runtime_state=False)
    assert config_off.inject_runtime_state is False
    assert config_off.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_load_injection_config_respects_system_flag(monkeypatch):
    from app.services.ai.runtime.agentscope import agent_runtime as ar

    monkeypatch.setattr(
        "app.services.platform_timezone.get_cached_platform_timezone",
        lambda: "UTC",
    )

    async def _fake_get(key: str):
        if key == "agentscope_inject_runtime_state":
            return "false"
        if key == "agentscope_inject_time_interval_hours":
            return "2"
        return None

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get",
        staticmethod(_fake_get),
    )

    config = await ar.load_injection_config()
    assert config.inject_runtime_state is False
    assert config.time_interval == 2.0


def test_agentscope_injection_migrations_exist():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    mysql = (root / "db-prod" / "V119-add_agentscope_injection_config.sql").read_text(
        encoding="utf-8"
    )
    pg = (root / "db-prod-pg" / "V19-add_agentscope_injection_config.sql").read_text(
        encoding="utf-8"
    )
    for text in (mysql, pg):
        assert "agentscope_inject_runtime_state" in text
        assert "agentscope_inject_time_interval_hours" in text


@pytest.mark.asyncio
async def test_tool_permission_middleware_passthrough_preserves_decision():
    middleware = ToolPermissionMiddleware(
        user_id=1,
        conversation_id="c1",
        agent_name="assistant",
    )
    expected = PermissionDecision(
        behavior=PermissionBehavior.ASK,
        message="need confirm",
        decision_reason="test",
    )

    async def next_handler(**kwargs):
        assert "tool" in kwargs
        return expected

    result = await middleware.on_check_permission(
        agent=SimpleNamespace(name="assistant"),
        input_kwargs={
            "tool_call": SimpleNamespace(name="Write"),
            "tool": SimpleNamespace(name="Write"),
            "tool_input": {"path": "a.txt"},
        },
        next_handler=next_handler,
    )
    assert result is expected
    assert result.behavior == PermissionBehavior.ASK


@pytest.mark.asyncio
async def test_tool_permission_middleware_deny_override_only_tightens():
    middleware = ToolPermissionMiddleware(
        user_id=1,
        conversation_id="c1",
        deny_override=lambda **kwargs: PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="blocked",
            decision_reason="override",
            bypass_immune=True,
        ),
    )
    allow = PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="ok",
        decision_reason="base",
    )

    async def next_handler(**kwargs):
        return allow

    result = await middleware.on_check_permission(
        agent=SimpleNamespace(name="a"),
        input_kwargs={
            "tool_call": SimpleNamespace(name="Bash"),
            "tool": SimpleNamespace(name="Bash"),
            "tool_input": {},
        },
        next_handler=next_handler,
    )
    assert result.behavior == PermissionBehavior.DENY
    assert result.decision_reason == "override"


@pytest.mark.asyncio
async def test_tool_permission_middleware_ignores_non_deny_override():
    middleware = ToolPermissionMiddleware(
        user_id=1,
        conversation_id="c1",
        deny_override=lambda **kwargs: PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="should ignore",
            decision_reason="bad_override",
        ),
    )
    ask = PermissionDecision(
        behavior=PermissionBehavior.ASK,
        message="need confirm",
        decision_reason="base",
    )

    async def next_handler(**kwargs):
        return ask

    result = await middleware.on_check_permission(
        agent=SimpleNamespace(name="a"),
        input_kwargs={
            "tool_call": SimpleNamespace(name="Write"),
            "tool": SimpleNamespace(name="Write"),
            "tool_input": {},
        },
        next_handler=next_handler,
    )
    assert result is ask
    assert result.behavior == PermissionBehavior.ASK


def test_extract_msg_structured_output_reads_dict_and_model():
    assert extract_msg_structured_output(None) is None
    assert extract_msg_structured_output(SimpleNamespace()) is None
    assert extract_msg_structured_output({"structured_output": {"a": 1}}) == {"a": 1}
    assert extract_msg_structured_output(SimpleNamespace(structured_output={"b": 2})) == {
        "b": 2
    }

    class _Model:
        def model_dump(self):
            return {"c": 3}

    assert extract_msg_structured_output(SimpleNamespace(structured_output=_Model())) == {
        "c": 3
    }


def test_build_runtime_middlewares_includes_permission_and_stats():
    from app.services.ai.runtime.agentscope.agent_runtime import build_runtime_middlewares
    from app.services.ai.runtime.agentscope.middleware import (
        ModelCallStatsMiddleware,
        ToolPermissionMiddleware,
    )

    mws = build_runtime_middlewares(
        user_id=7,
        conversation_id="conv",
        agent_name="Friday",
        trace_id="t1",
    )
    assert isinstance(mws[0], ToolPermissionMiddleware)
    assert isinstance(mws[1], ModelCallStatsMiddleware)

    mws_no_conv = build_runtime_middlewares(user_id=7, conversation_id=None)
    assert len(mws_no_conv) == 1
    assert isinstance(mws_no_conv[0], ToolPermissionMiddleware)


@pytest.mark.asyncio
async def test_tool_permission_middleware_forbidden_tools_deny_override(monkeypatch):
    from agentscope.permission import PermissionBehavior, PermissionDecision
    from app.services.ai.runtime.agentscope.middleware import ToolPermissionMiddleware

    async def _deny(tool_name, explicit_user_id=None):
        assert tool_name == "Bash"
        assert explicit_user_id == 9
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="forbidden",
            decision_reason="hit_user_forbidden_tool",
            bypass_immune=True,
        )

    monkeypatch.setattr(
        "app.services.ai.runtime.agentscope.tools.enforce_tool_forbidden",
        _deny,
    )

    from app.services.ai.runtime.agentscope.agent_runtime import build_runtime_middlewares

    mws = build_runtime_middlewares(user_id=9, conversation_id="c", agent_name="a")
    middleware = mws[0]
    assert isinstance(middleware, ToolPermissionMiddleware)

    allow = PermissionDecision(
        behavior=PermissionBehavior.ALLOW,
        message="ok",
        decision_reason="base",
    )

    async def next_handler(**kwargs):
        return allow

    result = await middleware.on_check_permission(
        agent=SimpleNamespace(name="a"),
        input_kwargs={
            "tool_call": SimpleNamespace(name="Bash"),
            "tool": SimpleNamespace(name="Bash"),
            "tool_input": {},
        },
        next_handler=next_handler,
    )
    assert result.behavior == PermissionBehavior.DENY
    assert result.decision_reason == "hit_user_forbidden_tool"


def test_parse_semantic_intent_data_from_dict():
    from app.services.ai.data_query_semantic_intent import parse_semantic_intent_data

    intent = parse_semantic_intent_data(
        {
            "keywords": "机房 机柜",
            "goal": "查机房",
            "metrics": ["机柜数"],
            "dimensions": ["机房"],
            "filters": [],
        },
        fallback_question="查机房",
    )
    assert intent.keywords == "机房 机柜"
    assert intent.metrics == ["机柜数"]


@pytest.mark.asyncio
async def test_try_generate_semantic_intent_structured_fail_open():
    from app.services.ai.data_query_semantic_intent import (
        try_generate_semantic_intent_structured,
    )

    payload, response = await try_generate_semantic_intent_structured(None, "prompt")
    assert payload is None and response is None

    class _Broken:
        async def generate_structured_output(self, **kwargs):
            raise RuntimeError("boom")

    payload, response = await try_generate_semantic_intent_structured(_Broken(), "prompt")
    assert payload is None and response is None


@pytest.mark.asyncio
async def test_try_generate_semantic_intent_structured_success():
    from app.services.ai.data_query_semantic_intent import (
        try_generate_semantic_intent_structured,
    )

    class _Ok:
        async def generate_structured_output(self, **kwargs):
            assert "structured_model" in kwargs
            return SimpleNamespace(
                content={"keywords": "销售额", "goal": "查销售", "metrics": ["销售额"]},
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    payload, response = await try_generate_semantic_intent_structured(_Ok(), "prompt")
    assert payload["keywords"] == "销售额"
    assert response.usage.input_tokens == 10


def test_requirements_pins_agentscope_exact_version():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / "requirements.txt").read_text(
        encoding="utf-8"
    )
    # 必须精确锁定版本：无上限会让镜像重建时静默拉到当时的最新正式版，造成本地
    # .venv 与生产镜像版本漂移（升级说明见 docs/md/agentscope_2.0.9_upgrade_guide.md）
    assert re.search(
        r"^agentscope\[service,storage,workspace\]==\d+\.\d+\.\d+$",
        text,
        re.MULTILINE,
    ), "requirements.txt 必须精确锁定 agentscope 版本（含 runtime extras）"
