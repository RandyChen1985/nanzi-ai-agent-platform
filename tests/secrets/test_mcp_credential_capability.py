"""MCP 的模型视图必须说明凭据模式，同时不泄露绑定细节。"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core import context as agent_context
from app.models.mcp import McpToolCache
from app.models.secret import SecretBinding
from app.services.ai.runtime.agentscope.tools import runtime_tool_spec_from_legacy_tool
from app.services.ai.tools import registry
from app.services.ai.tools.mcp_factory import McpToolFactory

pytestmark = pytest.mark.no_infrastructure


def record(*, operation="health", params=("ping",)):
    return SimpleNamespace(
        id="fixture-tool", server_id="fixture-server", tool_name=f"fixture:{operation}",
        tool_description="synthetic-remote-description", credential_params=json.dumps(list(params)),
        parameter_schema=json.dumps({"type": "object", "properties": {
            **{name: {"type": "string"} for name in params}, "query": {"type": "string"},
        }, "required": list(params)}),
    )


@pytest.mark.parametrize("mode", ["fixed", "infer", "ask"])
def test_profile_mode_reaches_model_without_internal_references(mode):
    profile = SimpleNamespace(
        profile_id="synthetic-profile", revision=2, selection_mode=mode,
        mappings={"ping": "password"}, type_key="fixture_mail", type_version=3,
    )
    tool = McpToolFactory.create_tool(
        record(), credential_profile=profile, credential_type_name="测试邮箱", verify_profile=True,
    )
    spec = runtime_tool_spec_from_legacy_tool(tool, source_type="mcp")
    assert spec.metadata.get("mcp_credential") == {
        "mode": mode, "candidate_lookup_required": mode == "infer",
        "credential_type": {"name": "测试邮箱", "type_key": "fixture_mail", "version": 3},
    }
    assert "服务健康检查" in spec.description
    assert ("credential_account" in spec.parameters_schema["properties"]) == (mode == "infer")
    assert "synthetic-" not in repr((spec.description, spec.parameters_schema, spec.metadata["mcp_credential"]))
    if mode == "fixed":
        assert "固定" in spec.description and "无需查询" in spec.description
    elif mode == "infer":
        assert "list_mcp_credential_candidates" in spec.description
    else:
        assert "服务端交互" in spec.description


@pytest.fixture
def bound_registry(monkeypatch):
    """仅替换工具与配置查询；绑定筛选在内存数据库中执行真实 SQL。"""
    engine = create_engine("sqlite://")
    SecretBinding.__table__.create(engine)
    state = SimpleNamespace(tool=record(), user_id=101, fail_bindings=False, selected_columns=[])

    class SessionAdapter:
        async def get(self, model, key):
            return None

        async def execute(self, statement):
            if statement.column_descriptions[0].get("entity") is McpToolCache:
                return SimpleNamespace(scalar_one_or_none=lambda: state.tool)
            state.selected_columns.append(set(statement.selected_columns.keys()))
            if state.fail_bindings:
                raise RuntimeError("synthetic-binding-query-failure")
            return session.execute(statement)

    @asynccontextmanager
    async def sessions():
        yield SessionAdapter()

    monkeypatch.setattr(registry, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(agent_context, "get_current_agent_context", lambda: SimpleNamespace(user_id=state.user_id))
    registry.ToolRegistry.clear_db_tool_cache()
    with Session(engine) as session:
        state.session = session
        yield state
    registry.ToolRegistry.clear_db_tool_cache()
    engine.dispose()


def bind(state, owner, strategy, *, parameter="ping", server="fixture-server"):
    state.session.add(SecretBinding(
        id=f"fixture-{state.session.query(SecretBinding).count()}",
        consumer_type="mcp_server", consumer_id=server,
        slot=f"arg:health:{parameter}", bound_by_user_id=owner, strategy=strategy,
        secret_id="synthetic-secret-reference" if strategy == "fixed" else None,
        rule_json='{"on_miss":"model"}' if strategy == "rule" else None,
    ))
    state.session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(("bindings", "expected", "choice"), [
    ([("", "fixed")], "fixed", False),
    ([("", "rule"), ("101", "fixed")], "fixed", False),
    ([("", "fixed"), ("101", "rule"), ("101", "fixed")], "rule", True),
    ([("102", "fixed")], "parameter_bindings", True),
    ([], "parameter_bindings", True),
])
async def test_registry_uses_effective_parameter_binding_mode(bound_registry, bindings, expected, choice):
    state = bound_registry
    for owner, strategy in bindings:
        bind(state, owner, strategy)
    # 其他服务的规则不能改变当前工具的模式。
    bind(state, "101", "rule", server="other-server")
    loaded = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert loaded is not None
    tool, source = loaded
    spec = runtime_tool_spec_from_legacy_tool(tool, source_type=source)
    assert spec.metadata.get("mcp_credential") == {"mode": expected, "candidate_lookup_required": False}
    assert ("credential_choice_1" in spec.parameters_schema["properties"]) == choice
    assert "ping" not in repr((spec.description, spec.parameters_schema, spec.metadata["mcp_credential"]))
    assert "synthetic-" not in repr((spec.description, spec.parameters_schema, spec.metadata["mcp_credential"]))
    assert state.selected_columns == [{"slot", "strategy", "bound_by_user_id"}]


@pytest.mark.asyncio
async def test_registry_keeps_binding_modes_isolated_per_user(bound_registry):
    state = bound_registry
    bind(state, "", "fixed")
    bind(state, "101", "rule")
    first, _ = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    state.user_id = 102
    second, _ = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert first.runtime_metadata.get("mcp_credential", {}).get("mode") == "rule"
    assert second.runtime_metadata.get("mcp_credential", {}).get("mode") == "fixed"
    assert first is not second


@pytest.mark.asyncio
async def test_registry_refreshes_parameter_mode_after_binding_changes_without_cache_clear(bound_registry):
    state = bound_registry
    bind(state, "", "fixed")
    fixed, _ = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert fixed.runtime_metadata["mcp_credential"]["mode"] == "fixed"
    assert "credential_choice_1" not in fixed.mcp_input_schema["properties"]

    bind(state, "101", "rule")
    rule, _ = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert rule.runtime_metadata["mcp_credential"]["mode"] == "rule"
    assert "credential_choice_1" in rule.mcp_input_schema["properties"]

    state.session.query(SecretBinding).filter_by(bound_by_user_id="101", strategy="rule").delete()
    state.session.flush()
    restored, _ = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert restored.runtime_metadata["mcp_credential"]["mode"] == "fixed"
    assert "credential_choice_1" not in restored.mcp_input_schema["properties"]


@pytest.mark.asyncio
async def test_registry_mixed_binding_modes_only_offer_choice_for_rule_parameter(bound_registry):
    state = bound_registry
    state.tool = record(params=("ping", "mailkey"))
    bind(state, "", "fixed")
    bind(state, "", "rule", parameter="mailkey")
    tool, _ = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert tool.runtime_metadata.get("mcp_credential") == {"mode": "mixed", "candidate_lookup_required": False}
    assert set(tool.mcp_input_schema["properties"]) == {"query", "credential_choice_2"}


@pytest.mark.asyncio
async def test_binding_query_failure_preserves_tool_with_unknown_mode(bound_registry):
    state = bound_registry
    state.fail_bindings = True
    loaded = await registry.ToolRegistry._load_db_tool_with_source(state.tool.tool_name)
    assert loaded is not None
    tool, _ = loaded
    assert tool.runtime_metadata.get("mcp_credential") == {
        "mode": "parameter_bindings", "candidate_lookup_required": False,
    }
    assert "固定" not in tool.description
    assert "credential_choice_1" in tool.mcp_input_schema["properties"]


@pytest.mark.parametrize("operation", ["health", "health_check", "healthcheck", "unhealthy", "health_delete"])
def test_health_purpose_uses_exact_local_operation_mapping(operation):
    tool = McpToolFactory.create_tool(record(operation=operation, params=()), verify_profile=True)
    assert ("服务健康检查" in tool.description) == (operation in {"health", "health_check", "healthcheck"})
    assert "synthetic-remote-description" not in tool.description
    assert tool.runtime_metadata.get("mcp_credential") == {"mode": "none", "candidate_lookup_required": False}


def test_public_credential_info_drops_internal_fields_and_recomputes_candidate_flag():
    from app.services.secrets.mcp_capability import public_credential_info

    assert public_credential_info({
        "mode": "fixed", "candidate_lookup_required": True, "secret_id": "synthetic-ref",
        "credential_type": {"name": "测试邮箱", "type_key": "mail", "version": 2, "payload": "synthetic-value"},
    }) == {
        "mode": "fixed", "candidate_lookup_required": False,
        "credential_type": {"name": "测试邮箱", "type_key": "mail", "version": 2},
    }
    assert public_credential_info({"mode": "unsupported"}) == {}
    assert public_credential_info(None) == {}
