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
        '{"intent":"DATA_QUERY","confidence":0.91,"reasoning":"查询业务数据","entities":["PUE"],"domain":"chatbi_business_data","operation":"lookup"}'
    )

    with patch(
        "app.services.ai.intent_service.chat_client_from_handle",
        return_value=chat_client,
    ):
        result = await service.identify_intent("查询上海机房 PUE", llm=llm)

    assert result.intent == IntentType.DATA_QUERY
    assert result.confidence == 0.91
    assert result.domain == "chatbi_business_data"
    assert result.operation == "lookup"
    called_messages = chat_client.generate_text.await_args.args[0]
    assert called_messages[0].role == "system"
    assert called_messages[1].role == "user"
    assert "查询上海机房 PUE" == called_messages[1].content[0].text


@pytest.mark.asyncio
async def test_intent_service_parses_fact_freshness_and_time_scope():
    service = IntentService()
    llm = object()
    chat_client = _mock_chat_client(
        '{"intent":"DATA_QUERY","confidence":0.93,"reasoning":"查询当前机器状态",'
        '"entities":["load"],"domain":"runtime_environment","operation":"lookup",'
        '"fact_kind":"machine_load","freshness_requirement":"realtime",'
        '"time_scope":null}'
    )

    with patch(
        "app.services.ai.intent_service.chat_client_from_handle",
        return_value=chat_client,
    ):
        result = await service.identify_intent("查询当前机器负载", llm=llm)

    assert result.fact_kind == "machine_load"
    assert result.freshness_requirement == "realtime"
    assert result.time_scope is None
    assert "freshness_requirement" in service._format_instructions()


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


def test_intent_service_marks_legacy_data_response_as_unqualified():
    result = IntentService._parse_response(
        '{"intent":"DATA_QUERY","confidence":0.9,"reasoning":"旧格式","entities":[]}'
    )

    assert result.domain == "unqualified_data_intent"
    assert result.operation == "unknown"


@pytest.mark.asyncio
async def test_intent_service_parses_tool_call_as_knowledge_base():
    service = IntentService()
    llm = object()
    chat_client = _mock_chat_client(
        '{"tool": "search_knowledge_base", "query": "换电", "dataset_ids": "4525d66cec7111f0a3d00242ac120006"}'
    )

    with patch(
        "app.services.ai.intent_service.chat_client_from_handle",
        return_value=chat_client,
    ):
        result = await service.identify_intent("换电过程中可以开门吗？", llm=llm)

    assert result.intent == IntentType.KNOWLEDGE_BASE
    assert result.confidence == 0.9


@pytest.mark.asyncio
async def test_intent_service_returns_unknown_when_llm_missing():
    service = IntentService()

    with patch("app.core.llm.client.get_llm_async", AsyncMock(return_value=None)):
        result = await service.identify_intent("你好")

    assert result.intent == IntentType.UNKNOWN
    assert result.confidence == 0.0
