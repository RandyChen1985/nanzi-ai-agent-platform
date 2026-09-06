from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app.services.ai.runtime.agentscope.models import AgentScopeModelConfig, create_openai_chat_model

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [0, 1, 3])
@pytest.mark.parametrize('status', [429, 503])
async def test_public_model_call_has_one_retry_budget(monkeypatch, budget, status):
    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'message': 'temporary failure'}})

    model = create_openai_chat_model(AgentScopeModelConfig(
        api_key='test-key', base_url='https://example.invalid/v1',
        model='test-model', max_retries=budget,
    ))
    # 替换传输层，保留模型与 SDK 的实际重试配置；禁止真实网络请求。
    await model.client._client.aclose()
    model.client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    try:
        with pytest.raises(openai.APIStatusError):
            await model([])
        assert len(calls) == budget + 1
    finally:
        await model.client.close()


@pytest.mark.asyncio
async def test_public_model_call_recovers_after_transient_error(monkeypatch):
    calls = []

    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={'error': {'message': 'temporary failure'}})
        return httpx.Response(200, json={
            'id': 'test', 'object': 'chat.completion', 'created': 0, 'model': 'test-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}],
        })

    model = create_openai_chat_model(AgentScopeModelConfig(
        api_key='test-key', base_url='https://example.invalid/v1',
        model='test-model', max_retries=1, streaming=False,
    ))
    await model.client._client.aclose()
    model.client._client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    try:
        result = await model([])
        assert len(calls) == 2
        assert result is not None
    finally:
        await model.client.close()
