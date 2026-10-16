"""契约：模型流的 chunk 间隔应用层超时（read_timeout 失效时的兜底）。

背景（真实故障）：一次真实会话在「模型调用 · DeepSeek-V4-Pro」处静默挂起 9 分钟，
事件日志序号停在 72 不动，既没有超时异常、也没有产生重试事件，最后只能等 600s 的
producer 硬超时看门狗兜底——用户被锁在「AI 正在努力生成回复中」整整十分钟。

配置链路上 `llm_request_read_timeout=180` 是生效、也确实传给了 SDK 的，但真实链路
里没有抛出任何超时。既然 httpx 的 read timeout 不可依赖（网关心跳/中间层都可能让
"有数据到达"一直成立），就在应用层按「相邻两个 chunk 的最大间隔」自己兜一层。

这里同时锁定一条闭环：超时抛出的异常必须能被既有的 `is_retryable_stream_error`
识别，否则重试链路依然不会触发——那才是这个故障真正致命的地方。
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.ai.runtime.agentscope.stream_idle_guard import (
    DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
    ModelStreamIdleTimeoutError,
    guard_model_stream_idle,
    parse_stream_idle_timeout,
)

pytestmark = pytest.mark.no_infrastructure


class _FakeModel:
    """最小可用的假模型：只有 astream 和一个普通属性。"""

    def __init__(self, delays, chunks=("a", "b", "c")) -> None:
        self.delays = list(delays)
        self.chunks = list(chunks)
        self.closed = False
        self.temperature = 0.3

    async def astream(self, *_args, **_kwargs):
        try:
            for index, chunk in enumerate(self.chunks):
                delay = self.delays[index] if index < len(self.delays) else 0
                if delay:
                    await asyncio.sleep(delay)
                yield chunk
        finally:
            self.closed = True


def _collect(model, timeout):
    async def run():
        guarded = guard_model_stream_idle(model, timeout)
        return [chunk async for chunk in guarded.astream("prompt")]

    return asyncio.run(run())


def test_healthy_stream_passes_through_untouched():
    assert _collect(_FakeModel(delays=[0, 0, 0]), 1.0) == ["a", "b", "c"]


def test_silent_stream_raises_idle_timeout():
    """chunk 间隔超过阈值即判定挂起，不再无限干等。"""
    with pytest.raises(ModelStreamIdleTimeoutError):
        _collect(_FakeModel(delays=[0, 5.0, 0]), 0.05)


def test_slow_but_alive_stream_is_not_killed():
    """只要还在产出就不能误杀：间隔小于阈值时慢速流必须放行。"""
    assert _collect(_FakeModel(delays=[0.02, 0.02, 0.02]), 0.5) == ["a", "b", "c"]


def test_inner_stream_is_closed_on_timeout():
    """超时后必须关掉底层流，避免连接和生成器悬挂。"""
    model = _FakeModel(delays=[0, 5.0, 0])
    with pytest.raises(ModelStreamIdleTimeoutError):
        _collect(model, 0.05)
    assert model.closed is True


def test_other_attributes_are_proxied_to_the_inner_model():
    guarded = guard_model_stream_idle(_FakeModel(delays=[0]), 1.0)
    assert guarded.temperature == 0.3


def test_call_is_forwarded_to_the_inner_model():
    """__getattr__ 不代理特殊方法，不显式转发的话 model(...) 会直接断掉。"""

    class _Callable(_FakeModel):
        async def __call__(self, *_args, **_kwargs):
            return "called"

    guarded = guard_model_stream_idle(_Callable(delays=[0]), 1.0)
    assert asyncio.run(guarded()) == "called"


def test_call_returning_an_async_stream_is_guarded():
    """AgentScope 的流式路径就是 __call__ 返回生成器，这条必须守住。"""

    class _Streaming(_FakeModel):
        async def __call__(self, *_args, **_kwargs):
            return self.astream()

    guarded = guard_model_stream_idle(_Streaming(delays=[0, 5.0, 0]), 0.05)

    async def run():
        stream = await guarded()
        return [chunk async for chunk in stream]

    with pytest.raises(ModelStreamIdleTimeoutError):
        asyncio.run(run())


def test_call_returning_a_plain_response_is_returned_as_is():
    class _Plain(_FakeModel):
        async def __call__(self, *_args, **_kwargs):
            return "response"

    guarded = guard_model_stream_idle(_Plain(delays=[0]), 1.0)
    assert asyncio.run(guarded()) == "response"


def test_isinstance_checks_still_match_the_inner_model():
    class _BaseModel:
        pass

    class _ConcreteModel(_BaseModel, _FakeModel):
        pass

    guarded = guard_model_stream_idle(_ConcreteModel(delays=[0]), 1.0)
    assert isinstance(guarded, _BaseModel) is True


def test_get_chat_model_wraps_the_native_model():
    """接线契约：模型必须在唯一的构建出口就被包装。

    放在 get_chat_model 里才覆盖得到同步的 get_llm——主助手链路走的是那条。
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[3] / "app/core/llm/client.py").read_text(
        encoding="utf-8"
    )
    assert "native_model=guard_model_stream_idle(native_model, read_timeout)" in source


def test_non_positive_timeout_disables_the_guard():
    model = _FakeModel(delays=[0])
    assert guard_model_stream_idle(model, 0) is model
    assert guard_model_stream_idle(model, -1) is model
    assert guard_model_stream_idle(None, 180) is None


def test_default_timeout_matches_the_read_timeout_budget():
    assert DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS == 180.0


def test_idle_timeout_is_classified_as_retryable():
    """闭环：超时必须被既有重试逻辑识别，否则永远不会重试。"""
    from app.services.ai.executors.common import is_retryable_stream_error

    error = ModelStreamIdleTimeoutError("model stream idle timeout: no chunk for 180s")
    assert is_retryable_stream_error(error) is True


def test_parse_rejects_garbage_and_keeps_zero_as_disabled():
    assert parse_stream_idle_timeout(None) == DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS
    assert parse_stream_idle_timeout("abc") == DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS
    assert parse_stream_idle_timeout(True) == DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS
    assert parse_stream_idle_timeout("90") == 90.0
    assert parse_stream_idle_timeout(0) == 0.0
    assert parse_stream_idle_timeout(-5) == 0.0
