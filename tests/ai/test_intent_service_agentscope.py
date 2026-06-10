from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.intent_service import IntentService, IntentType


pytestmark = pytest.mark.no_infrastructure


def _mock_chat_client(content: str):
    chat_client = AsyncMock()
    chat_client.generate_text.return_value = content
    return chat_client


@pytest.mark.asyncio
async def test_intent_service_uses_agentscope_chat_client():
    service = IntentService()
    llm = object()
    chat_client = _mock_chat_client(
        '{"intent":"DATA_QUERY","confidence":0.91,"reasoning":"查询业务数据","entities":["PUE"]}'
    )

    with patch(
        "app.services.ai.intent_service.chat_client_from_handle",
        return_value=chat_client,
    ):
        result = await service.identify_intent("查询上海机房 PUE", llm=llm)

    assert result.intent == IntentType.DATA_QUERY
    assert result.confidence == 0.91
    called_messages = chat_client.generate_text.await_args.args[0]
    assert called_messages[0].role == "system"
    assert called_messages[1].role == "user"
    assert "查询上海机房 PUE" == called_messages[1].content[0].text


@pytest.mark.asyncio
async def test_intent_service_parses_json_inside_text():
    service = IntentService()
    llm = object()
    chat_client = _mock_chat_client(
        '好的：{"intent":"GENERAL","confidence":0.8,"reasoning":"闲聊","entities":[]}'
    )

    with patch(
        "app.services.ai.intent_service.chat_client_from_handle",
        return_value=chat_client,
    ):
        result = await service.identify_intent("你好", llm=llm)

    assert result.intent == IntentType.GENERAL
    assert result.reasoning == "闲聊"


@pytest.mark.asyncio
async def test_intent_service_returns_unknown_when_llm_missing():
    service = IntentService()

    with patch("app.core.llm.client.get_llm_async", AsyncMock(return_value=None)):
        result = await service.identify_intent("你好")

    assert result.intent == IntentType.UNKNOWN
    assert result.confidence == 0.0
