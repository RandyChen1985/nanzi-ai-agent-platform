"""模型调用失败的日志留痕契约。

背景：AgentScope 的 ``Agent._call_model`` 在 fallback 模型接管成功后会丢弃主模型的
异常，只留一行 "exhausted all N attempt(s)"，平台侧也没有任何地方记录上游返回的
响应体。线上因此只能看到 400/404，看不到原因（例如 ``model not found``）。

约定：平台在自己的 ``_call_api`` 边界补上留痕——
1. 任何失败（HTTP 非 2xx、连接错误、流式中断）都记录模型名、状态码、上游响应摘要；
2. 只记录，不改控制流：原异常原样抛出，返回值原样透传；
3. 记录本身出错不得影响调用。
"""

import logging
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_infrastructure

LOGGER_NAME = "app.services.ai.runtime.agentscope.models"
FAILURE_MARKER = "Model call failed"
MODEL_NAME = "deepseek-v4.1-flash"
BASE_URL = "https://llm.example.com/v1"


def _build_status_error(exc_type, status_code, message, *, code="model_not_found"):
    """用真实 openai SDK 异常复现上游非 2xx 响应。"""
    import httpx

    request = httpx.Request("POST", f"{BASE_URL}/chat/completions")
    body = {"error": {"code": code, "message": message}}
    response = httpx.Response(status_code, json=body, request=request)
    return exc_type(
        f"Error code: {status_code} - {message}",
        response=response,
        body=body,
    )


def _build_connection_error():
    import httpx
    import openai

    request = httpx.Request("POST", f"{BASE_URL}/chat/completions")
    return openai.APIConnectionError(request=request)


def _patch_client(monkeypatch, handler):
    """把 openai.AsyncClient 换成由 handler 决定行为的假客户端。"""
    import openai

    class FakeCompletions:
        async def create(self, **kwargs):
            return await handler(kwargs)

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(openai, "AsyncClient", lambda **kwargs: FakeClient())


def _build_model(*, streaming=False, thinking_enable=False):
    from app.services.ai.runtime.agentscope.models import (
        AgentScopeModelConfig,
        create_openai_chat_model,
    )

    return create_openai_chat_model(
        AgentScopeModelConfig(
            api_key="sk-test",
            base_url=BASE_URL,
            model=MODEL_NAME,
            provider="deepseek",
            streaming=streaming,
            thinking_enable=thinking_enable,
            thinking_capable=thinking_enable,
        )
    )


def _failure_logs(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME and FAILURE_MARKER in record.getMessage()
    ]


def _stream_chunk(text):
    return SimpleNamespace(
        id="resp-1",
        usage=None,
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=text,
                    reasoning_content=None,
                    reasoning=None,
                    audio=None,
                    tool_calls=None,
                )
            )
        ],
    )


class _InterruptingStream:
    """先吐正常 chunk，再在迭代中抛错，模拟流式过程中断。"""

    def __init__(self, chunks, error):
        self._chunks = chunks
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        raise self._error


@pytest.mark.asyncio
async def test_bad_request_failure_is_logged_and_still_raises(monkeypatch, caplog):
    import openai

    async def handler(_kwargs):
        raise _build_status_error(
            openai.BadRequestError,
            400,
            "model `deepseek-v4.1-flash` not found",
        )

    _patch_client(monkeypatch, handler)
    model = _build_model()

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(openai.BadRequestError):
            await model._call_api(MODEL_NAME, messages=[])

    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert MODEL_NAME in logs[0]
    assert "status=400" in logs[0]
    assert "not found" in logs[0]
    assert "model_not_found" in logs[0]
    assert BASE_URL in logs[0]


@pytest.mark.asyncio
async def test_not_found_failure_is_logged_and_still_raises(monkeypatch, caplog):
    """非 BadRequestError 的 HTTP 失败同样要留痕。"""
    import openai

    async def handler(_kwargs):
        raise _build_status_error(
            openai.NotFoundError,
            404,
            "no such route",
        )

    _patch_client(monkeypatch, handler)
    model = _build_model()

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(openai.NotFoundError):
            await model._call_api(MODEL_NAME, messages=[])

    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert "status=404" in logs[0]
    assert "no such route" in logs[0]


@pytest.mark.asyncio
async def test_connection_failure_is_logged_and_still_raises(monkeypatch, caplog):
    """没有 HTTP 状态码的连接层失败也要留下可排查的痕迹。"""
    import openai

    async def handler(_kwargs):
        raise _build_connection_error()

    _patch_client(monkeypatch, handler)
    model = _build_model()

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(openai.APIConnectionError):
            await model._call_api(MODEL_NAME, messages=[])

    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert MODEL_NAME in logs[0]
    assert "APIConnectionError" in logs[0]


@pytest.mark.asyncio
async def test_stream_interruption_is_logged_and_chunks_still_pass_through(
    monkeypatch,
    caplog,
):
    """流式过程中断：日志留痕、异常照抛、正常 chunk 不受影响。"""
    import openai

    async def handler(_kwargs):
        return _InterruptingStream(
            [_stream_chunk("你好")],
            _build_connection_error(),
        )

    _patch_client(monkeypatch, handler)
    model = _build_model(streaming=True)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        stream = await model._call_api(MODEL_NAME, messages=[])
        received = []
        with pytest.raises(openai.APIConnectionError):
            async for chunk in stream:
                received.append(chunk)

    assert received, "正常 chunk 必须原样透传"
    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert MODEL_NAME in logs[0]
    assert "APIConnectionError" in logs[0]


@pytest.mark.asyncio
async def test_recovered_thinking_tool_choice_retry_is_not_logged_as_failure(
    monkeypatch,
    caplog,
):
    """已知可恢复的 400（思考模式拒收强制 tool_choice）重试成功后不算失败。"""
    from agentscope.tool import ToolChoice

    from app.services.ai.runtime.agentscope.models import (
        AgentScopeModelConfig,
        create_openai_chat_model,
    )

    import openai

    attempts = []

    async def handler(_kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise _build_status_error(
                openai.BadRequestError,
                400,
                "The tool_choice parameter does not support being set to "
                "required or object in thinking mode",
                code="invalid_parameter_error",
            )
        return SimpleNamespace(choices=[], usage=None)

    _patch_client(monkeypatch, handler)
    model = create_openai_chat_model(
        AgentScopeModelConfig(
            api_key="sk-test",
            base_url=BASE_URL,
            model=MODEL_NAME,
            provider="deepseek",
            streaming=False,
            thinking_enable=True,
            thinking_capable=True,
        )
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        await model._call_api(
            MODEL_NAME,
            messages=[],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "execute_sql_query",
                        "parameters": {"type": "object"},
                    },
                },
            ],
            tool_choice=ToolChoice(mode="execute_sql_query"),
        )

    assert len(attempts) == 2, "应禁用思考后重试一次"
    assert _failure_logs(caplog) == []


@pytest.mark.asyncio
async def test_failed_thinking_tool_choice_retry_is_logged(monkeypatch, caplog):
    """重试仍然失败时，必须记录上游详情。"""
    from agentscope.tool import ToolChoice

    from app.services.ai.runtime.agentscope.models import (
        AgentScopeModelConfig,
        create_openai_chat_model,
    )

    import openai

    async def handler(_kwargs):
        raise _build_status_error(
            openai.BadRequestError,
            400,
            "The tool_choice parameter does not support being set to "
            "required or object in thinking mode",
            code="invalid_parameter_error",
        )

    _patch_client(monkeypatch, handler)
    model = create_openai_chat_model(
        AgentScopeModelConfig(
            api_key="sk-test",
            base_url=BASE_URL,
            model=MODEL_NAME,
            provider="deepseek",
            streaming=False,
            thinking_enable=True,
            thinking_capable=True,
        )
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(openai.BadRequestError):
            await model._call_api(
                MODEL_NAME,
                messages=[],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "execute_sql_query",
                            "parameters": {"type": "object"},
                        },
                    },
                ],
                tool_choice=ToolChoice(mode="execute_sql_query"),
            )

    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert "status=400" in logs[0]
    assert "invalid_parameter_error" in logs[0]


@pytest.mark.asyncio
async def test_non_streaming_result_is_not_wrapped_into_a_stream(monkeypatch):
    """非流式返回值必须仍是 ChatResponse：失败日志包装不得改变返回类型。

    基类靠 ``isinstance(res, ChatResponse)`` 区分流式／非流式，一旦这里被
    包成异步生成器，非流式调用会被静默破坏（且不会被任何断言发现）。
    """
    from agentscope.model import ChatResponse

    async def handler(_kwargs):
        return SimpleNamespace(
            id="resp-1",
            model=MODEL_NAME,
            created=0,
            object="chat.completion",
            usage=None,
            choices=[],
        )

    _patch_client(monkeypatch, handler)
    model = _build_model(streaming=False)

    result = await model._call_api(MODEL_NAME, messages=[])

    assert isinstance(result, ChatResponse)
    assert not hasattr(result, "__aiter__")


@pytest.mark.asyncio
async def test_broken_error_object_does_not_break_the_call(monkeypatch, caplog):
    """记录过程本身出错时，原始异常必须原样抛出，且回退到最小可用日志。"""

    class _WeirdError(Exception):
        @property
        def body(self):
            raise RuntimeError("body is unreadable")

        @property
        def response(self):
            raise RuntimeError("response is unreadable")

    async def handler(_kwargs):
        raise _WeirdError("upstream exploded")

    _patch_client(monkeypatch, handler)
    model = _build_model()

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        with pytest.raises(_WeirdError):
            await model._call_api(MODEL_NAME, messages=[])

    logs = _failure_logs(caplog)
    assert len(logs) == 1
    assert MODEL_NAME in logs[0]
    assert "_WeirdError" in logs[0]
