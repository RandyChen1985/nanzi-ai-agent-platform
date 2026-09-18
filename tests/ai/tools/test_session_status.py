import importlib
import json
import platform
import sys
from pathlib import Path

import pytest

from app.core.context import AgentContext, get_current_agent_context, set_agent_context, set_debug_context
from app.services.ai.tools.registry import ToolRegistry


pytestmark = pytest.mark.no_infrastructure


# 默认注入一个显式来源（agent_config）且带 context_size 的运行时模型信息，
# 用于验证动态截断水位线语义；需要回归配置兜底时传 runtime_model_info={}。
_DEFAULT_RUNTIME_MODEL_INFO = {
    "configured_model": "primary-alias",
    "effective_model_id": "provider-model",
    "source": "agent_config",
    "phase": "primary_agent",
    "is_fallback": False,
    "resolution_status": "registry_resolved",
    "context_size": 131072,
    "max_output_tokens": 8192,
    "provider": "openai-compatible",
    "thinking_enable": True,
    "thinking_capable": True,
    "reasoning_effort": "medium",
    "api_key": "must-not-leak",
    "base_url": "https://secret.example.invalid/v1",
}


def _session_status_tool():
    tool = ToolRegistry._registry.get("session_status")
    assert tool is not None, "session_status must be registered before it can be invoked"
    return tool


def _runtime_capabilities_tool():
    tool = ToolRegistry._registry.get("get_runtime_capabilities")
    assert tool is not None, "get_runtime_capabilities must be registered before it can be invoked"
    return tool


def _install_context(
    *,
    conversation_id: str | None = "conv-1",
    runtime_model_info: dict | None = _DEFAULT_RUNTIME_MODEL_INFO,
) -> None:
    set_agent_context(
        AgentContext(
            agent_id="agent-1",
            agent_name="助手",
            engine_type="LOCAL",
            user_id=123,
            conversation_id=conversation_id,
            is_admin=False,
            user_dimensions={
                "id": 123,
                "user_name": "alice",
                "real_name": "Alice",
                "role": "analyst",
                "dept_code": "D01",
                "org_path": "总部/数据部",
                "extra_data": {"api_key": "must-not-leak"},
            },
            dataset_ids=["dataset-1"],
            knowledge_dataset_ids=["kb-1"],
            metadata_dataset_ids=["meta-1"],
            skills=["skill-1"],
            runtime_model_info=dict(runtime_model_info or {}),
            authorized_attachment_paths=["/private/attachment/sales.xlsx"],
            current_turn_attachment_paths=["/private/attachment/sales.xlsx"],
            permission_options={"mode": "allow"},
        )
    )
    set_debug_context(
        {
            "injected_context": {
                "device_type": "移动端(小屏幕)",
                "display_hint": "窄屏排版优化",
            }
        }
    )


def _install_infra_mocks(
    monkeypatch,
    *,
    history=None,
    budget="65536",
    budget_side_effect=None,
):
    """安全桩掉 session_status 会触碰的基础设施调用。

    默认 memory 无历史 -> 估算返回 None 占位；budget 可配置以覆盖真实估算路径。
    """
    module = importlib.import_module("app.services.ai.tools.session_status")

    async def fake_get_history(user_id, conversation_id, limit=None, offset=0):
        return history if history is not None else []

    monkeypatch.setattr(module.memory_service, "get_history", fake_get_history)

    async def fake_config_get(key, default=None):
        if budget_side_effect is not None:
            raise budget_side_effect
        return budget

    import app.services.config_service as config_service

    monkeypatch.setattr(
        config_service.ConfigService,
        "get",
        fake_config_get,
    )


def _install_workspace_redis_mocks(tmp_path, monkeypatch, *, redis=None):
    module = importlib.import_module("app.services.ai.tools.session_status")

    async def fake_workspace_root(**_kwargs):
        return str(tmp_path)

    monkeypatch.setattr(module, "resolve_workspace_root", fake_workspace_root)

    if redis is not None:
        async def fake_get_redis():
            return redis
        monkeypatch.setattr(module, "get_redis", fake_get_redis)
    else:
        async def fake_no_redis():
            return None
        monkeypatch.setattr(module, "get_redis", fake_no_redis)


def test_session_status_returns_safe_current_context_snapshot(tmp_path: Path, monkeypatch):
    _install_context()
    get_current_agent_context().runtime_tool_capabilities = [
        {"name": "mcp_sales_search", "source_type": "mcp"}
    ]
    module = importlib.import_module("app.services.ai.tools.session_status")

    user_root = tmp_path / "alice__123"
    session_workdir = user_root / "sessions" / "conv-1"
    docs_dir = user_root / "docs"
    session_workdir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)

    class FakeRedis:
        async def lrange(self, key, start, end):
            return [
                json.dumps(
                    {
                        "input_tokens": 1200,
                        "output_tokens": 80,
                        "timestamp": "2026-08-15T10:00:00+00:00",
                    }
                )
            ]

    _install_workspace_redis_mocks(tmp_path, monkeypatch, redis=FakeRedis())
    _install_infra_mocks(monkeypatch)

    payload = json.loads(_session_status_tool().invoke({}))

    assert payload["scope"] == "current_session"
    assert payload["session"]["conversation_id"] == "conv-1"
    assert payload["session"]["agent_name"] == "助手"
    assert payload["client"] == {
        "device_type": "移动端(小屏幕)",
        "display_hint": "窄屏排版优化",
        "source": "client_reported",
    }
    runtime_env = payload["runtime_env"]
    assert runtime_env["env_kind"] in ("docker", "host")
    assert runtime_env["python"]["version"].count(".") == 2
    assert runtime_env["python"]["major"] >= 3
    assert runtime_env["platform"]["os"] == sys.platform
    assert runtime_env["python"]["version"] == ".".join(
        str(v)
        for v in (
            runtime_env["python"]["major"],
            runtime_env["python"]["minor"],
            runtime_env["python"]["micro"],
        )
    )
    assert payload["workspace"]["session_workdir"] == {
        "path": str(session_workdir),
        "exists": True,
        "writable": True,
    }
    assert payload["workspace"]["docs_dir"] == {
        "path": str(docs_dir),
        "exists": True,
        "writable": True,
        "scope": "cross_session",
    }
    assert payload["model"]["context_window_tokens"] == 131072
    assert payload["context_usage"]["last_input_tokens"] == 1200
    assert payload["context_usage"]["last_output_tokens"] == 80
    assert payload["user"]["user_name"] == "alice"
    assert payload["attachments"] == {
        "authorized_count": 1,
        "current_turn_count": 1,
        "filenames": ["sales.xlsx"],
    }
    assert payload["resources"]["mcp_tools"] == [
        {"name": "mcp_sales_search", "source_type": "mcp", "status": "mounted"}
    ]
    # 没有配置 memory 历史 => 估算路径返回 None 占位（不抛错）
    assert payload["context_usage"]["estimated_current_tokens"] is None
    assert payload["context_usage"]["estimated_remaining_tokens"] is None
    assert payload["context_usage"]["context_messages"] is None


def test_session_status_context_usage_estimates_full_history_against_budget(
    tmp_path: Path, monkeypatch
):
    # 无显式模型窗口的 context => 以兜底窗口 4096 扣除 overhead 后得到历史预算。
    _install_context(runtime_model_info={})
    module = importlib.import_module("app.services.ai.tools.session_status")

    history = [
        {"role": "user", "content": "你好，我需要一些帮助", "tool_run_text": ""},
        {"role": "assistant", "content": "好的，请说", "tool_run_text": ""},
        {"role": "assistant", "content": "", "tool_run_text": "tool_call_for_something"},
    ]
    _install_workspace_redis_mocks(tmp_path, monkeypatch)
    _install_infra_mocks(monkeypatch, history=history, budget="4096")

    payload = json.loads(_session_status_tool().invoke({}))
    usage = payload["context_usage"]

    expected_total = sum(
        module.estimate_text_tokens(
            str(msg.get("content") or "") + str(msg.get("tool_run_text") or "")
        )
        for msg in history
    )
    assert usage["context_messages"] == len(history)
    assert usage["physical_window"] == 4096
    assert usage["history_budget"] == 1365
    assert usage["token_budget"] == 1365
    assert usage["estimated_current_tokens"] == expected_total
    assert usage["estimated_remaining_tokens"] == max(0, 1365 - expected_total)
    assert usage["usage_percentage"] == round(expected_total / 1365 * 100, 1)

    # 最近一次模型调用统计与全量估算并存，语义不同，均保留
    assert "last_input_tokens" in usage
    assert "estimated_current_tokens" in usage


def test_session_status_context_usage_adopts_explicit_model_window(
    tmp_path: Path, monkeypatch
):
    # 显式来源（agent_config）且带 context_size=131072 => 物理窗口取模型窗口，
    # 历史预算再扣除 overhead，确保与 agent_service 动态截断水位线口径一致。
    _install_context()
    module = importlib.import_module("app.services.ai.tools.session_status")

    history = [
        {"role": "user", "content": "你好，我需要一些帮助", "tool_run_text": ""},
        {"role": "assistant", "content": "好的，请说", "tool_run_text": ""},
    ]
    _install_workspace_redis_mocks(tmp_path, monkeypatch)
    _install_infra_mocks(monkeypatch, history=history, budget="4096")

    payload = json.loads(_session_status_tool().invoke({}))
    usage = payload["context_usage"]

    expected_total = sum(
        module.estimate_text_tokens(
            str(msg.get("content") or "") + str(msg.get("tool_run_text") or "")
        )
        for msg in history
    )
    assert usage["physical_window"] == 131072
    # overhead 现在是模块常量（不再是系统配置项），因此不能假设它等于桩里的 budget；
    # 期望值直接由同一个常量推导，避免测试与实现各持一份魔数。
    from app.services.ai.context_usage import CONTEXT_OVERHEAD_RESERVATION_TOKENS

    expected_budget = 131072 - CONTEXT_OVERHEAD_RESERVATION_TOKENS
    assert usage["history_budget"] == expected_budget
    assert usage["token_budget"] == expected_budget
    assert usage["estimated_remaining_tokens"] == max(0, expected_budget - expected_total)
    assert usage["usage_percentage"] == round(expected_total / expected_budget * 100, 1)


def test_session_status_context_usage_ignores_system_default_window(
    tmp_path: Path, monkeypatch
):
    # system_default 来源即使带 context_size 也不应覆盖配置兜底，
    # 防止默认模型 1M 窗口稀释 64k 截断水位线 -> 提前 compat 的问题被反向放大。
    _install_context(
        runtime_model_info={
            "source": "system_default",
            "effective_model_id": "default-model",
            "configured_model": "default-model",
            "context_size": 1048576,
        }
    )
    module = importlib.import_module("app.services.ai.tools.session_status")
    history = [
        {"role": "user", "content": "你好，我需要一些帮助", "tool_run_text": ""},
    ]
    _install_workspace_redis_mocks(tmp_path, monkeypatch)
    _install_infra_mocks(monkeypatch, history=history, budget="4096")

    payload = json.loads(_session_status_tool().invoke({}))
    usage = payload["context_usage"]
    assert usage["physical_window"] == 4096
    assert usage["token_budget"] == 1365


def test_session_status_context_usage_falls_back_when_estimation_fails(
    tmp_path: Path, monkeypatch
):
    _install_context()
    _install_workspace_redis_mocks(tmp_path, monkeypatch)
    _install_infra_mocks(monkeypatch, budget_side_effect=RuntimeError("boom"))

    module = importlib.import_module("app.services.ai.tools.session_status")

    async def exploding_get_history(user_id, conversation_id, limit=None, offset=0):
        raise RuntimeError("db down")

    monkeypatch.setattr(module.memory_service, "get_history", exploding_get_history)

    payload = json.loads(_session_status_tool().invoke({}))
    usage = payload["context_usage"]
    assert usage["estimated_current_tokens"] is None
    assert usage["estimated_remaining_tokens"] is None
    assert usage["context_messages"] is None
    assert usage["token_budget"] is None
    assert usage["usage_percentage"] is None


def test_session_status_excludes_secrets_and_internal_objects(tmp_path: Path, monkeypatch):
    _install_context()
    module = importlib.import_module("app.services.ai.tools.session_status")

    _install_workspace_redis_mocks(tmp_path, monkeypatch)
    _install_infra_mocks(monkeypatch)

    payload = json.loads(_session_status_tool().invoke({}))
    serialized = json.dumps(payload, ensure_ascii=False)

    assert "api_key" not in serialized
    assert "base_url" not in serialized
    assert "engine_config" not in serialized
    assert "permission_options" not in serialized
    assert "/private/attachment" not in serialized
    assert "must-not-leak" not in serialized


def test_session_status_is_parameterless_and_handles_missing_context(monkeypatch):
    set_agent_context(None)
    set_debug_context({})

    tool = _session_status_tool()
    assert tool.args_schema.model_fields == {}

    module = importlib.import_module("app.services.ai.tools.session_status")

    async def fake_get_redis():
        return None

    monkeypatch.setattr(module, "get_redis", fake_get_redis)
    _install_infra_mocks(monkeypatch)
    payload = json.loads(tool.invoke({}))

    assert payload["scope"] == "current_session"
    assert payload["session"]["conversation_id"] is None
    assert payload["user"] is None
    assert payload["limitations"]


@pytest.mark.asyncio
async def test_workspace_root_read_only_resolution_does_not_create_directory(tmp_path, monkeypatch):
    from app.services.ai.runtime.agentscope import workspace

    missing_root = tmp_path / "not-created"
    monkeypatch.setattr(workspace, "default_workspace_root", lambda: str(missing_root))

    resolved = await workspace.resolve_workspace_root(ensure_exists=False)

    assert resolved == str(missing_root)
    assert not missing_root.exists()


def test_session_status_runtime_env_is_readonly_and_degrades_on_probe_failure(
    tmp_path: Path, monkeypatch
):
    _install_context()
    module = importlib.import_module("app.services.ai.tools.session_status")
    _install_workspace_redis_mocks(tmp_path, monkeypatch)
    _install_infra_mocks(monkeypatch)

    import app.utils.env as env_module

    # 正常情况下 env_kind 应为 docker / host 之一，且与平台探测一致
    monkeypatch.setattr(env_module, "get_env", lambda: "docker")
    payload = json.loads(_session_status_tool().invoke({}))
    assert payload["runtime_env"]["env_kind"] == "docker"
    assert payload["runtime_env"]["platform"]["system"] == platform.system()
    assert payload["runtime_env"]["platform"]["machine"] == platform.machine()

    # 探测抛异常 => 只读降级：env_kind=None 且计入 limitations，不抛错
    def exploding_get_env():
        raise RuntimeError("probe down")

    monkeypatch.setattr(env_module, "get_env", exploding_get_env)
    payload = json.loads(_session_status_tool().invoke({}))
    assert payload["runtime_env"]["env_kind"] is None
    assert any("env_kind" in msg for msg in payload["limitations"])


@pytest.mark.asyncio
async def test_session_status_is_read_only_runtime_state_tool():
    from app.services.ai.grounding.models import EvidenceType

    spec = await ToolRegistry.get_runtime_tool("session_status")

    assert spec.permission_scope == "read"
    assert spec.is_read_only is True
    assert spec.evidence_types == frozenset({EvidenceType.RUNTIME_STATE})
    assert "不要猜测" in spec.description


def test_runtime_capabilities_reports_only_currently_mounted_tools():
    _install_context()
    context = get_current_agent_context()
    context.runtime_tool_capabilities = [
        {
            "name": "session_status",
            "source_type": "system",
            "permission_scope": "read",
            "capability": "session_status",
            "source": "runtime_environment",
            "side_effect": "read",
            "confirmation": "none",
        },
        {
            "name": "mcp_sales_search",
            "source_type": "mcp",
            "permission_scope": "read",
            "capability": "unknown",
            "source": "mcp",
            "side_effect": "read",
            "confirmation": "unknown",
        },
    ]

    payload = json.loads(_runtime_capabilities_tool().invoke({}))

    assert payload["scope"] == "current_runtime"
    assert [item["name"] for item in payload["tools"]] == [
        "session_status",
        "mcp_sales_search",
    ]
    assert payload["tools"][1]["source_type"] == "mcp"
    assert payload["tools"][1]["permission_scope"] == "read"
    assert "不代表所有已注册工具" in payload["limitations"][0]


@pytest.mark.asyncio
async def test_runtime_capabilities_is_read_only_system_tool():
    from app.services.ai.grounding.models import EvidenceType

    spec = await ToolRegistry.get_runtime_tool("get_runtime_capabilities")

    assert spec.permission_scope == "read"
    assert spec.is_read_only is True
    assert spec.evidence_types == frozenset({EvidenceType.RUNTIME_STATE})
