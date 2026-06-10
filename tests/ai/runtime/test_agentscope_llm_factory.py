from types import SimpleNamespace
from typing import AsyncIterator

import pytest


pytestmark = pytest.mark.no_infrastructure


def test_llm_factory_builds_agentscope_model_config(monkeypatch):
    from app.core.llm.client import LLMFactory
    from app.services.ai.runtime.agentscope.models import AgentScopeModelConfig

    captured = {}

    def fake_create_model(config: AgentScopeModelConfig):
        captured["config"] = config
        return SimpleNamespace(model=config.model, stream=config.streaming)

    monkeypatch.setattr(
        "app.core.llm.client.create_openai_chat_model",
        fake_create_model,
    )

    model = LLMFactory.get_chat_model(
        streaming=True,
        api_key="sk-test",
        base_url="https://llm.example.com/v1",
        model="deepseek-chat",
        temperature=0.2,
    )

    assert captured["config"] == AgentScopeModelConfig(
        api_key="sk-test",
        base_url="https://llm.example.com/v1",
        model="deepseek-chat",
        temperature=0.2,
        streaming=True,
    )
    assert model.model_name == "deepseek-chat"
    assert model.temperature == 0.2
    assert model.native_model.model == "deepseek-chat"


def test_create_openai_chat_model_uses_agentscope_parameters():
    from app.services.ai.runtime.agentscope.models import (
        AgentScopeModelConfig,
        create_openai_chat_model,
    )

    model = create_openai_chat_model(
        AgentScopeModelConfig(
            api_key="sk-test",
            base_url="https://llm.example.com/v1",
            model="deepseek-chat",
            temperature=0.25,
            streaming=False,
        )
    )

    assert model.model == "deepseek-chat"
    assert model.stream is False
    assert model.parameters.temperature == 0.25


@pytest.mark.asyncio
async def test_get_llm_async_uses_config_service_fallbacks(monkeypatch):
    from app.core.llm import client

    async def fake_config_get(key):
        values = {
            "llm_model_name": "configured-model",
            "llm_api_key": "configured-key",
            "llm_base_url": "https://configured.example/v1",
            "llm_temperature": "0.3",
        }
        return values.get(key)

    captured = {}

    def fake_get_chat_model(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model_name=kwargs["model"])

    monkeypatch.setattr(client.ConfigServiceProxy, "get", staticmethod(fake_config_get))
    monkeypatch.setattr(client.LLMFactory, "get_chat_model", staticmethod(fake_get_chat_model))
    monkeypatch.setattr(client, "_lookup_ai_model_record", lambda model: None)

    model = await client.get_llm_async(streaming=False)

    assert model.model_name == "configured-model"
    assert captured == {
        "streaming": False,
        "api_key": "configured-key",
        "base_url": "https://configured.example/v1",
        "model": "configured-model",
        "temperature": 0.3,
    }


@pytest.mark.asyncio
async def test_get_llm_async_prefers_model_registry_credentials(monkeypatch):
    from app.core.llm import client

    async def fake_config_get(key):
        values = {
            "llm_model_name": "friendly-name",
            "llm_api_key": "system-key",
            "llm_base_url": "https://system.example/v1",
            "llm_temperature": "0.4",
        }
        return values.get(key)

    captured = {}

    def fake_get_chat_model(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model_name=kwargs["model"])

    monkeypatch.setattr(client.ConfigServiceProxy, "get", staticmethod(fake_config_get))
    monkeypatch.setattr(client.LLMFactory, "get_chat_model", staticmethod(fake_get_chat_model))
    monkeypatch.setattr(
        client,
        "_lookup_ai_model_record",
        lambda model: SimpleNamespace(
            api_key="model-key",
            api_base_url="https://model.example/v1",
            model_id="real-model-id",
        ),
    )

    await client.get_llm_async(streaming=True)

    assert captured["api_key"] == "model-key"
    assert captured["base_url"] == "https://model.example/v1"
    assert captured["model"] == "real-model-id"
    assert captured["temperature"] == 0.4


@pytest.mark.asyncio
async def test_llm_handle_provides_executor_compatible_methods():
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle

    class ArgsSchema:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {"query": {"type": "string"}}}

    class Tool:
        name = "search"
        description = "Search docs"
        args_schema = ArgsSchema

    class FakeNativeModel:
        async def __call__(self, messages, **kwargs):
            self.messages = messages
            self.kwargs = kwargs
            return ChatResponse(content=[TextBlock(text="ok")], is_last=True)

    native_model = FakeNativeModel()
    handle = AgentScopeLLMHandle(
        native_model=native_model,
        model_name="test-model",
        temperature=0.0,
        streaming=False,
    ).bind_tools([Tool()])

    response = await handle.ainvoke("hello")

    assert response.content == "ok"
    assert native_model.kwargs["tools"][0]["function"]["name"] == "search"


@pytest.mark.asyncio
async def test_llm_handle_streams_executor_compatible_chunks():
    from agentscope.message import TextBlock
    from agentscope.model import ChatResponse

    from app.core.llm.client import AgentScopeLLMHandle

    async def stream() -> AsyncIterator[ChatResponse]:
        yield ChatResponse(content=[TextBlock(text="a")], is_last=False)
        yield ChatResponse(content=[TextBlock(text="b")], is_last=True)

    class FakeNativeModel:
        async def __call__(self, messages, **kwargs):
            return stream()

    handle = AgentScopeLLMHandle(
        native_model=FakeNativeModel(),
        model_name="test-model",
        temperature=0.0,
        streaming=True,
    )

    chunks = []
    async for chunk in handle.astream("hello"):
        chunks.append(chunk.content)

    assert chunks == ["a", "b"]
