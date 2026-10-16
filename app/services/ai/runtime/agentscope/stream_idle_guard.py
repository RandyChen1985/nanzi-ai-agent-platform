"""模型流的 chunk 间隔应用层超时。

真实故障：一次会话在「模型调用 · DeepSeek-V4-Pro」处静默挂起 9 分钟，事件日志序号
停在 72 不动，既没有超时异常、也没有产生重试事件，最后只能等 600s 的 producer 硬超时
看门狗兜底——用户被锁在「AI 正在努力生成回复中」整整十分钟。

配置链路上 ``llm_request_read_timeout=180`` 确实生效并传给了 SDK，但真实链路里没有抛出
任何超时。httpx 的 read timeout 语义是「相邻两次数据到达的间隔」，网关心跳或中间层只要
持续有字节到达，"静默"就永远不成立。因此这里在应用层按「相邻两个 chunk 的最大间隔」
自己兜一层：超时会让调用方拿到一个可重试的异常，从而走既有重试链路。

只做一件事：把 aiter 的下一次 ``__anext__`` 包进 ``asyncio.wait_for``。
其余属性一律透传给内层模型，避免影响 AgentScope 对模型对象的其它用法。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

#: 默认预算与 read timeout 对齐：2 次尝试合计 360s，仍在 600s 看门狗之内。
#: 单独调参用既有的 ``llm_request_read_timeout`` 即可，不再另开配置键。
DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS = 180.0


class ModelStreamIdleTimeoutError(asyncio.TimeoutError):
    """模型流在阈值内没有产出下一个 chunk。

    继承 ``asyncio.TimeoutError`` 以保持既有超时语义；消息里显式带 "timeout"，
    这样 ``is_retryable_stream_error`` 能识别它并触发重试——否则超时了也不会重试，
    正是这次故障最致命的一环。
    """


def parse_stream_idle_timeout(
    value: Any,
    *,
    default: float = DEFAULT_STREAM_IDLE_TIMEOUT_SECONDS,
) -> float:
    """解析空闲超时；非法值回退默认值，``<= 0`` 表示显式关闭该保护。"""
    if isinstance(value, bool):
        return default
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return default
    if number <= 0:
        return 0.0
    return number


class _IdleGuardedChatModel:
    """只包裹 ``astream``，其余属性透传。"""

    def __init__(self, inner: Any, idle_timeout: float) -> None:
        self._inner = inner
        self._idle_timeout = idle_timeout

    def __getattr__(self, name: str) -> Any:
        # 只会在实例字典找不到时触发；`_inner` 在 __init__ 里写入了实例字典。
        return getattr(self._inner, name)

    @property
    def __class__(self):  # type: ignore[override]
        """让 isinstance / 类型判断仍按内层模型走，避免包装被识别成陌生类型。"""
        return self._inner.__class__

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """转发调用，并在返回异步流时套上同一套空闲守卫。

        AgentScope 的 ``ChatModelBase.__call__`` 在流式模式下**返回**
        ``AsyncGenerator[ChatResponse, None]``（这个 async 方法本身被 await 一次，
        拿到的是生成器），非流式模式返回 ``ChatResponse``。真实故障就发生在这条
        路径上——只守 ``astream`` 是守不住的。

        ``__getattr__`` 对隐式特殊方法查找不生效，所以必须显式定义，否则
        ``await model(...)`` 会直接抛 "object is not callable"。
        """
        result = await self._inner(*args, **kwargs)
        if hasattr(result, "__anext__"):
            return self._guard(result)
        return result

    def astream(self, *args: Any, **kwargs: Any) -> Any:
        """平台自定义 LLM 的异步流入口（``async for chunk in model.astream()`）。"""
        return self._guard(self._inner.astream(*args, **kwargs))

    async def _guard(self, stream: Any) -> AsyncIterator[Any]:
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=self._idle_timeout
                    )
                except StopAsyncIteration:
                    return
                except asyncio.TimeoutError as exc:
                    logger.error(
                        "[LLMStreamIdle] no chunk for %.0fs; aborting this stream so the "
                        "caller can retry instead of waiting for the producer watchdog",
                        self._idle_timeout,
                    )
                    raise ModelStreamIdleTimeoutError(
                        f"model stream idle timeout: no chunk for {self._idle_timeout:.0f}s"
                    ) from exc
                yield chunk
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # noqa: BLE001 - 清理失败不得影响主流程
                    pass


def guard_model_stream_idle(model: Any, idle_timeout: Any) -> Any:
    """给模型流的 chunk 间隔加应用层超时；无法包装时原样返回。"""
    if model is None:
        return model
    timeout = parse_stream_idle_timeout(idle_timeout)
    if timeout <= 0:
        return model
    if not (hasattr(model, "astream") or callable(model)):
        return model
    return _IdleGuardedChatModel(model, timeout)
