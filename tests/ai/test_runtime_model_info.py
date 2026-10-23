from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.config import RuntimeModelInfo, resolve_runtime_model_info


pytestmark = pytest.mark.no_infrastructure


def test_runtime_model_info_public_payload_contains_no_secrets():
    info = RuntimeModelInfo(
        configured_model="团队默认模型",
        effective_model_id="deepseek-chat",
        source="agent_config",
        phase="primary_agent",
        is_fallback=False,
        resolution_status="registry_resolved",
    )

    payload = info.public_dict()

    assert payload == {
        "configured_model": "团队默认模型",
        "effective_model_id": "deepseek-chat",
        "source": "agent_config",
        "phase": "primary_agent",
        "is_fallback": False,
        "resolution_status": "registry_resolved",
    }
    assert "api_key" not in payload
    assert "base_url" not in payload


def test_runtime_model_info_public_payload_includes_safe_capacity_metadata():
    info = RuntimeModelInfo(
        configured_model="团队默认模型",
        effective_model_id="deepseek-chat",
        source="agent_config",
        context_size=131072,
        max_output_tokens=8192,
        provider="openai-compatible",
        thinking_enable=True,
        thinking_capable=True,
        reasoning_effort="medium",
    )

    payload = info.public_dict()

    assert payload["context_size"] == 131072
    assert payload["max_output_tokens"] == 8192
    assert payload["provider"] == "openai-compatible"
    assert payload["thinking_enable"] is True
    assert payload["thinking_capable"] is True
    assert payload["reasoning_effort"] == "medium"
    assert "api_key" not in payload
    assert "base_url" not in payload


@pytest.mark.asyncio
async def test_resolve_runtime_model_info_prefers_request_override():
    config = SimpleNamespace(model_name="agent-model")

    with patch(
        "app.services.ai.config.ConfigService.get_all_from_db",
        new=AsyncMock(return_value={"llm_model_name": {"value": "default-model"}}),
    ), patch(
        "app.services.ai.config._lookup_registered_model",
        new=AsyncMock(return_value=None),
    ):
        info = await resolve_runtime_model_info(
            config=config,
            execution_options={"model": "override-model"},
        )

    assert info.configured_model == "override-model"
    assert info.effective_model_id == "override-model"
    assert info.source == "request_override"
    assert info.resolution_status == "direct"


@pytest.mark.asyncio
async def test_resolve_runtime_model_info_maps_registered_alias_to_actual_id():
    config = SimpleNamespace(model_name="团队默认模型")
    registered = SimpleNamespace(name="团队默认模型", model_id="deepseek-chat")

    with patch(
        "app.services.ai.config.ConfigService.get_all_from_db",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.ai.config._lookup_registered_model",
        new=AsyncMock(return_value=registered),
    ):
        info = await resolve_runtime_model_info(config=config)

    assert info.configured_model == "团队默认模型"
    assert info.effective_model_id == "deepseek-chat"
    assert info.source == "agent_config"
    assert info.resolution_status == "registry_resolved"


@pytest.mark.asyncio
async def test_resolve_runtime_model_info_does_not_invent_registry_id_on_lookup_error():
    config = SimpleNamespace(model_name="agent-model")

    with patch(
        "app.services.ai.config.ConfigService.get_all_from_db",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.ai.config._lookup_registered_model",
        new=AsyncMock(side_effect=RuntimeError("database unavailable")),
    ):
        info = await resolve_runtime_model_info(config=config)

    assert info.effective_model_id == "agent-model"
    assert info.resolution_status == "registry_unresolved"
    assert info.public_dict()["effective_model_id"] == "agent-model"


@pytest.mark.asyncio
async def test_get_configured_llm_reuses_runtime_model_resolution(monkeypatch):
    from app.services.ai import config as config_module

    resolved = RuntimeModelInfo(
        configured_model="团队默认模型",
        effective_model_id="deepseek-chat",
        source="agent_config",
        resolution_status="registry_resolved",
    )
    resolve_mock = AsyncMock(return_value=resolved)
    monkeypatch.setattr(config_module, "resolve_runtime_model_info", resolve_mock)
    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(
            return_value={
                "llm_api_key": {"value": "system-key"},
                "llm_base_url": {"value": "https://system.example/v1"},
                "llm_temperature": {"value": "0.2"},
            }
        ),
    )
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model_name=kwargs["model"])

    monkeypatch.setattr(config_module, "get_llm", fake_get_llm)

    handle = await config_module.AgentConfigProvider.get_configured_llm(
        config=SimpleNamespace(model_name="团队默认模型", temperature=0.2)
    )

    assert handle.model_name == "deepseek-chat"
    assert captured["model"] == "deepseek-chat"
    resolve_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_configured_llm_passes_registered_token_limits(monkeypatch):
    from app.services.ai import config as config_module

    resolved = RuntimeModelInfo(
        configured_model="团队默认模型",
        effective_model_id="deepseek-chat",
        source="agent_config",
        resolution_status="registry_resolved",
        context_size=262144,
        max_output_tokens=65536,
    )
    monkeypatch.setattr(
        config_module,
        "resolve_runtime_model_info",
        AsyncMock(return_value=resolved),
    )
    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={"llm_api_key": {"value": "system-key"}}),
    )
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model_name=kwargs["model"])

    monkeypatch.setattr(config_module, "get_llm", fake_get_llm)

    await config_module.AgentConfigProvider.get_configured_llm(
        config=SimpleNamespace(model_name="团队默认模型", temperature=0.2)
    )

    assert captured["context_size"] == 262144
    assert captured["max_output_tokens"] == 65536
