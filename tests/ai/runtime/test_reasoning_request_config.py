from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_reasoning_defaults_to_thinking_only_for_registered_model():
    from app.services.ai.reasoning import resolve_reasoning_settings

    assert resolve_reasoning_settings(
        thinking_enable=True,
        thinking_only=True,
        reasoning_effort="high",
    ).thinking_enable is True
    assert resolve_reasoning_settings(
        thinking_enable=True,
        thinking_only=False,
        reasoning_effort="high",
    ).thinking_enable is False


def test_explicit_session_thinking_override_ignores_thinking_only():
    from app.services.ai.reasoning import resolve_reasoning_settings

    assert resolve_reasoning_settings(
        thinking_enable=True,
        thinking_only=False,
        reasoning_effort="high",
        overrides={"thinking_enable": True},
    ).thinking_enable is True
    assert resolve_reasoning_settings(
        thinking_enable=True,
        thinking_only=True,
        reasoning_effort="high",
        allow_disable_thinking=True,
        overrides={"thinking_enable": False},
    ).thinking_enable is False


def test_explicit_disable_only_requires_allow_disable_thinking():
    from app.services.ai.reasoning import resolve_reasoning_settings

    result = resolve_reasoning_settings(
        thinking_enable=True,
        thinking_only=True,
        reasoning_effort="high",
        allow_disable_thinking=False,
        overrides={"thinking_enable": False},
    )

    assert result.thinking_enable is True


def test_legacy_registered_reasoning_values_are_canonicalized():
    from app.services.ai.reasoning import resolve_reasoning_settings

    result = resolve_reasoning_settings(
        thinking_enable=True,
        thinking_only=True,
        reasoning_effort="max",
        supported_reasoning_efforts='["low", "high", "max"]',
    )

    assert result.reasoning_effort == "xhigh"


@pytest.mark.parametrize(
    ("thinking_enable", "reasoning_effort"),
    [(False, None), (True, None), (True, "low"), (True, "xhigh")],
)
def test_agentscope_model_config_carries_native_reasoning_parameters(
    thinking_enable,
    reasoning_effort,
):
    from app.services.ai.runtime.agentscope.models import AgentScopeModelConfig

    config = AgentScopeModelConfig(
        api_key="sk-test",
        base_url="https://llm.example.com/v1",
        model="thinking-model",
        thinking_enable=thinking_enable,
        reasoning_effort=reasoning_effort,
    )

    assert config.thinking_enable is thinking_enable
    assert config.reasoning_effort == reasoning_effort


@pytest.mark.asyncio
async def test_runtime_model_info_carries_registered_reasoning_configuration(monkeypatch):
    from app.services.ai import config as config_module

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(return_value=SimpleNamespace(
            model_id="registered-model",
            api_key=None,
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=True,
            reasoning_effort="max",
            supported_reasoning_efforts='["low", "high", "max"]',
        )),
    )

    info = await config_module.resolve_runtime_model_info(model_override="registered-model")

    assert info.thinking_enable is True
    assert info.thinking_capable is True
    assert info.reasoning_effort == "xhigh"
    assert info.supported_reasoning_efforts == ("low", "high", "xhigh")


@pytest.mark.asyncio
async def test_runtime_model_info_keeps_thinking_capable_when_default_thinking_is_off(monkeypatch):
    from app.services.ai import config as config_module

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(return_value=SimpleNamespace(
            model_id="thinking-model",
            api_key=None,
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=False,
            allow_disable_thinking=True,
            reasoning_effort="high",
            supported_reasoning_efforts=["low", "high"],
        )),
    )

    info = await config_module.resolve_runtime_model_info(model_override="thinking-model")

    assert info.thinking_enable is False
    assert info.thinking_capable is True
    assert info.reasoning_effort is None


@pytest.mark.asyncio
async def test_runtime_model_info_drops_invalid_registered_default_effort(monkeypatch):
    from app.services.ai import config as config_module

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(return_value=SimpleNamespace(
            model_id="thinking-model",
            api_key=None,
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=True,
            allow_disable_thinking=False,
            reasoning_effort="xhigh",
            supported_reasoning_efforts=["low", "high"],
        )),
    )

    info = await config_module.resolve_runtime_model_info(model_override="thinking-model")

    assert info.thinking_enable is True
    assert info.reasoning_effort is None


def test_llm_factory_carries_native_reasoning_parameters_into_model_config(monkeypatch):
    from app.core.llm.client import LLMFactory
    from app.services.ai.runtime.agentscope.models import AgentScopeModelConfig

    captured = {}

    def fake_create_model(config: AgentScopeModelConfig):
        captured["config"] = config
        return SimpleNamespace(model=config.model)

    monkeypatch.setattr(
        "app.core.llm.client.create_openai_chat_model",
        fake_create_model,
    )

    LLMFactory.get_chat_model(
        api_key="sk-test",
        base_url="https://llm.example.com/v1",
        model="thinking-model",
        thinking_enable=True,
        reasoning_effort="xhigh",
    )

    assert captured["config"].thinking_enable is True
    assert captured["config"].thinking_capable is False
    assert captured["config"].reasoning_effort == "xhigh"


@pytest.mark.asyncio
async def test_get_llm_async_reads_reasoning_configuration_from_registered_model(monkeypatch):
    from app.core.llm import client

    async def fake_config_get(key):
        return {
            "llm_model_name": "thinking-model",
            "llm_api_key": "system-key",
            "llm_base_url": "https://system.example/v1",
        }.get(key)

    captured = {}

    async def fake_lookup(model):
        return SimpleNamespace(
            model_id=model,
            api_key=None,
            api_base_url=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=True,
            reasoning_effort="xhigh",
        )

    def fake_get_chat_model(**kwargs):
        captured.update(kwargs)
        return "handle"

    monkeypatch.setattr(client.ConfigServiceProxy, "get", staticmethod(fake_config_get))
    monkeypatch.setattr(client, "_lookup_ai_model_record", fake_lookup)
    monkeypatch.setattr(client.LLMFactory, "get_chat_model", staticmethod(fake_get_chat_model))

    assert await client.get_llm_async(streaming=False) == "handle"
    assert captured["thinking_enable"] is True
    assert captured["thinking_capable"] is True
    assert captured["reasoning_effort"] == "xhigh"


@pytest.mark.asyncio
async def test_get_llm_async_applies_request_reasoning_override_to_auxiliary_calls(monkeypatch):
    from app.core.llm import client

    async def fake_config_get(key):
        return {
            "llm_model_name": "thinking-model",
            "llm_api_key": "system-key",
            "llm_base_url": "https://system.example/v1",
        }.get(key)

    async def fake_lookup(model):
        return SimpleNamespace(
            model_id=model,
            api_key=None,
            api_base_url=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=True,
            allow_disable_thinking=True,
            reasoning_effort="low",
            supported_reasoning_efforts=["low", "high"],
        )

    captured = {}

    def fake_get_chat_model(**kwargs):
        captured.update(kwargs)
        return "handle"

    monkeypatch.setattr(client.ConfigServiceProxy, "get", staticmethod(fake_config_get))
    monkeypatch.setattr(client, "_lookup_ai_model_record", fake_lookup)
    monkeypatch.setattr(client, "get_execution_option", lambda key, default=None: {
        "thinking_enable": False,
        "reasoning_effort": "high",
    }.get(key, default))
    monkeypatch.setattr(client.LLMFactory, "get_chat_model", staticmethod(fake_get_chat_model))

    assert await client.get_llm_async(streaming=False) == "handle"
    assert captured["thinking_enable"] is False
    assert captured["thinking_capable"] is True
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_get_llm_async_ignores_session_reasoning_override_when_requested(monkeypatch):
    from app.core.llm import client

    async def fake_config_get(key):
        return {
            "llm_model_name": "thinking-model",
            "llm_api_key": "system-key",
            "llm_base_url": "https://system.example/v1",
        }.get(key)

    async def fake_lookup(model):
        return SimpleNamespace(
            model_id=model,
            api_key=None,
            api_base_url=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=True,
            allow_disable_thinking=True,
            reasoning_effort="low",
            supported_reasoning_efforts=["low", "high"],
        )

    captured = {}

    def fake_get_chat_model(**kwargs):
        captured.update(kwargs)
        return "handle"

    monkeypatch.setattr(client.ConfigServiceProxy, "get", staticmethod(fake_config_get))
    monkeypatch.setattr(client, "_lookup_ai_model_record", fake_lookup)
    monkeypatch.setattr(client, "get_execution_option", lambda key, default=None: {
        "thinking_enable": False,
        "reasoning_effort": "high",
    }.get(key, default))
    monkeypatch.setattr(client.LLMFactory, "get_chat_model", staticmethod(fake_get_chat_model))

    assert await client.get_llm_async(
        streaming=False,
        ignore_session_reasoning_overrides=True,
    ) == "handle"
    assert captured["thinking_enable"] is True
    assert captured["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_configured_llm_passes_reasoning_configuration_to_shared_factory(monkeypatch):
    from app.services.ai import config as config_module

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(
            return_value=SimpleNamespace(
                model_id="registered-model",
                api_key=None,
                api_base_url=None,
                context_size=None,
                max_output_tokens=None,
                provider="openai",
                thinking_enable=True,
                thinking_only=True,
                reasoning_effort="low",
            )
        ),
    )
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return "handle"

    monkeypatch.setattr(config_module, "get_llm", fake_get_llm)

    assert await config_module.AgentConfigProvider.get_configured_llm(
        streaming=True,
        model_override="registered-model",
    ) == "handle"
    assert captured["thinking_enable"] is True
    assert captured["thinking_capable"] is True
    assert captured["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_request_reasoning_override_applies_only_when_registered_model_allows_it(monkeypatch):
    from app.services.ai import config as config_module

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(return_value=SimpleNamespace(
            model_id="thinking-model",
            api_key=None,
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=False,
            allow_disable_thinking=True,
            reasoning_effort="low",
            supported_reasoning_efforts=["low", "high", "xhigh"],
        )),
    )
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return "handle"

    monkeypatch.setattr(config_module, "get_llm", fake_get_llm)
    monkeypatch.setattr(
        config_module,
        "get_execution_option",
        lambda key, default=None: {
            "thinking_enable": False,
            "reasoning_effort": "high",
        }.get(key, default),
    )

    await config_module.AgentConfigProvider.get_configured_llm(
        streaming=True,
        model_override="thinking-model",
    )

    assert captured["thinking_enable"] is False
    assert captured["thinking_capable"] is True
    assert captured["reasoning_effort"] is None


@pytest.mark.asyncio
async def test_request_reasoning_override_ignores_unsupported_effort(monkeypatch):
    from app.services.ai import config as config_module

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(return_value=SimpleNamespace(
            model_id="thinking-model",
            api_key=None,
            api_base_url=None,
            context_size=None,
            max_output_tokens=None,
            provider="openai",
            thinking_enable=True,
            thinking_only=True,
            allow_disable_thinking=False,
            reasoning_effort=None,
            supported_reasoning_efforts=["low", "high"],
        )),
    )
    captured = {}

    def fake_get_llm(**kwargs):
        captured.update(kwargs)
        return "handle"

    monkeypatch.setattr(config_module, "get_llm", fake_get_llm)
    monkeypatch.setattr(
        config_module,
        "get_execution_option",
        lambda key, default=None: "xhigh" if key == "reasoning_effort" else default,
    )

    await config_module.AgentConfigProvider.get_configured_llm(
        streaming=True,
        model_override="thinking-model",
    )

    assert captured["thinking_enable"] is True
    assert captured["reasoning_effort"] is None
