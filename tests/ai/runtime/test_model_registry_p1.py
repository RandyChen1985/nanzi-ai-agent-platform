from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.ai.runtime.agentscope.request_timeout import (
    DEFAULT_LLM_REQUEST_READ_TIMEOUT,
)


pytestmark = pytest.mark.no_infrastructure


def test_model_registry_prefers_exact_model_id_over_display_name():
    from app.services.ai.model_registry import resolve_model_record

    exact = SimpleNamespace(model_id="gpt-4o", name="生产模型", is_active=True)
    name_match = SimpleNamespace(model_id="other-model", name="gpt-4o", is_active=True)

    assert resolve_model_record(exact_record=exact, named_records=[name_match]) is exact


def test_model_registry_allows_unique_display_name_as_legacy_alias():
    from app.services.ai.model_registry import resolve_model_record

    name_match = SimpleNamespace(model_id="deepseek-chat", name="团队默认", is_active=True)

    assert resolve_model_record(exact_record=None, named_records=[name_match]) is name_match


def test_model_registry_rejects_ambiguous_display_name():
    from app.services.ai.model_registry import (
        ModelNameAmbiguousError,
        resolve_model_record,
    )

    records = [
        SimpleNamespace(model_id="model-a", name="同名模型", is_active=True),
        SimpleNamespace(model_id="model-b", name="同名模型", is_active=True),
    ]

    with pytest.raises(ModelNameAmbiguousError):
        resolve_model_record(exact_record=None, named_records=records)


def test_model_registry_does_not_fall_back_when_exact_model_is_disabled():
    from app.services.ai.model_registry import ModelDisabledError, resolve_model_record

    disabled = SimpleNamespace(model_id="gpt-4o", name="生产模型", is_active=False)

    with pytest.raises(ModelDisabledError):
        resolve_model_record(exact_record=disabled, named_records=[])


@pytest.mark.asyncio
async def test_get_llm_async_propagates_disabled_model_instead_of_using_system_fallback(monkeypatch):
    from app.core.llm import client
    from app.services.ai.model_registry import ModelDisabledError

    async def fake_config_get(key):
        return {
            "llm_model_name": "gpt-4o",
            "llm_api_key": "system-key",
            "llm_base_url": "https://system.example/v1",
        }.get(key)

    monkeypatch.setattr(client.ConfigServiceProxy, "get", staticmethod(fake_config_get))
    monkeypatch.setattr(
        client,
        "_lookup_ai_model_record",
        lambda model: (_ for _ in ()).throw(ModelDisabledError("gpt-4o")),
    )

    with pytest.raises(ModelDisabledError):
        await client.get_llm_async(streaming=False)


@pytest.mark.asyncio
async def test_runtime_resolution_propagates_disabled_model(monkeypatch):
    from app.services.ai import config as config_module
    from app.services.ai.model_registry import ModelDisabledError

    monkeypatch.setattr(
        config_module.ConfigService,
        "get_all_from_db",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        config_module,
        "_lookup_registered_model",
        AsyncMock(
            side_effect=ModelDisabledError("gpt-4o")
        ),
    )

    with pytest.raises(ModelDisabledError):
        await config_module.resolve_runtime_model_info(model_override="gpt-4o")


def test_azure_model_config_contains_provider():
    from app.core.llm import client
    from app.services.ai.runtime.agentscope.models import AgentScopeModelConfig

    captured = {}

    def fake_create_model(config: AgentScopeModelConfig):
        captured["config"] = config
        return SimpleNamespace()

    original = client.create_openai_chat_model
    client.create_openai_chat_model = fake_create_model
    try:
        client.LLMFactory.get_chat_model(
            api_key="azure-key",
            base_url="https://resource.openai.azure.com",
            model="gpt-4o-prod",
            provider="azure",
        )
    finally:
        client.create_openai_chat_model = original

    assert captured["config"].provider == "azure"


def test_azure_chat_model_uses_deployment_endpoint_and_api_version():
    from app.services.ai.runtime.agentscope.models import (
        AgentScopeModelConfig,
        create_openai_chat_model,
    )

    model = create_openai_chat_model(
        AgentScopeModelConfig(
            api_key="azure-key",
            base_url="https://resource.openai.azure.com?api-version=2025-01-01",
            model="gpt-4o-prod",
            provider="azure",
        )
    )

    assert model.credential.base_url.endswith("/openai/deployments/gpt-4o-prod")
    # timeout 由平台统一注入（四字段显式给全），单独取出后校验，
    # 其余键保持全等比较以确保 azure 分支没有多写参数。
    client_kwargs = dict(model.client_kwargs)
    timeout = client_kwargs.pop("timeout", None)
    assert client_kwargs == {
        "max_retries": 0,
        "default_headers": {"api-key": "azure-key"},
        "default_query": {"api-version": "2025-01-01"},
    }
    assert timeout is not None
    assert timeout.read == DEFAULT_LLM_REQUEST_READ_TIMEOUT


@pytest.mark.asyncio
async def test_embedding_model_connection_uses_embeddings_endpoint(monkeypatch):
    from app.api.portal.endpoints import models as models_endpoint

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.request = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["request"] = (url, kwargs)
            return FakeResponse()

    monkeypatch.setattr(models_endpoint.httpx, "AsyncClient", FakeClient)

    result = await models_endpoint._test_model_connection(
        model_id="bge-m3",
        model_type="embedding",
        provider="siliconflow",
        api_key="sk-test",
        api_base_url="https://api.siliconflow.cn/v1",
    )

    assert result["status"] == "success"
    request = captured["request"]
    assert request[0] == "https://api.siliconflow.cn/v1/embeddings"
    assert request[1]["json"] == {"model": "bge-m3", "input": "ping"}
    assert request[1]["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_embedding_runtime_propagates_disabled_registry_model(monkeypatch):
    from app.services.ai import embedding_client
    from app.services.ai.model_registry import ModelDisabledError

    async def fake_resolve(use_global=False):
        return "https://embed.example/v1", "sk-test", "bge-m3"

    async def fake_lookup(model):
        raise ModelDisabledError(model)

    monkeypatch.setattr(
        embedding_client.EmbeddingClient,
        "_resolve_credentials",
        staticmethod(fake_resolve),
    )
    monkeypatch.setattr(
        "app.services.ai.model_registry.lookup_registered_model",
        fake_lookup,
    )

    with pytest.raises(ModelDisabledError):
        await embedding_client.EmbeddingClient.embed_text("hello", use_global=True)
