"""注入值的回显兜底。

模型看不到凭据、凭据只在出站那一刻由平台补进请求——这两层已经封住了。
但「工具把收到的参数原样回显」不受我们控制：远端服务、用户自己写的脚本、
某个调试接口，都可能把值带出来，随工具结果一起回到模型上下文。

这里在结果回灌之前做一次**精确匹配**：只扫本次请求真的注入过的那些值，
命中就掩码并记一条告警。范围极小，误伤也就极小——它不是主要防线，
是主要防线都失效时的最后一环。

为什么不做「扫全部凭据」：那要把所有凭据解密进内存，性能和风险都翻倍，
而真正的泄漏只可能来自本次注入的值。
"""

from __future__ import annotations

import logging
from contextvars import Context, ContextVar
from typing import Any, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

#: 太短的值扫了会误伤整个结果——"1"、"true"、"16" 在任何文本里都能命中。
#: 凭据通常远长于这个阈值；宁可漏掉极短的，也不要破坏正常内容。
MIN_LENGTH = 8

#: 一次请求可能连着调好几个工具，值会不断累积。设上限避免扫描线性变慢。
#:
#: 超过上限时**淘汰最旧的**，而不是拒绝新的。原来的写法是 `len >= MAX` 就
#: 直接 return，意味着「第 21 个凭据起完全得不到脱敏」——而那恰恰是当前这次
#: 调用正在用的值。旧值对应的调用已经结束，结果早就回灌完了；新值才是需要保护的。
MAX_VALUES = 20

_injected: ContextVar[tuple[str, ...]] = ContextVar(
    "injected_secret_values", default=()
)


def register_injected(value: str) -> None:
    """登记一条本次请求注入过的明文。由注入层在 `reveal()` 之后调用。

    只登记不落库：`ContextVar` 的生命周期就是这一次请求，请求结束自动失效，
    也天然按请求隔离——并发的两次对话不会互相看见对方的值。
    """
    clean = str(value or "")
    if len(clean) < MIN_LENGTH:
        return
    current = _injected.get()
    if clean in current:
        return
    if len(current) >= MAX_VALUES:
        current = current[-(MAX_VALUES - 1):]
    _injected.set(current + (clean,))


def reset_injected() -> None:
    """清掉当前 context 的登记。测试用，也留给长驻任务重置。"""
    _injected.set(())


def injected_count() -> int:
    """当前 context 登记了几条。只给测试和诊断用。"""
    return len(_injected.get())


def injected_values() -> tuple[str, ...]:
    """返回当前 context 已登记的明文元组，供脱敏审计与跨任务回流使用。"""
    return _injected.get()


def merge_injected(values: Iterable[str]) -> None:
    """把一组已注入明文合入当前 context（保持长度过滤、去重与上限淘汰语义）。"""
    for value in values:
        register_injected(value)


def collect_injected_from_context(ctx: Context) -> None:
    """把复制出去的子 Context 中登记的明文回收合并回当前 context。"""
    values = ctx.run(_injected.get)
    if values:
        merge_injected(values)


def _mask(value: str) -> str:
    """留头尾各 4 位，中间打码——够认出「是哪个值」，抄不走。"""
    return f"{value[:4]}***{value[-4:]}" if len(value) > 12 else "***"


def redact_text(text: str, *, where: str = "", log_hits: bool = True) -> str:
    """把文本里出现的已注入值换成掩码。

    先做 `in` 判断再 `replace`：绝大多数结果里一个都不会出现，那一次包含判断
    就返回了，不会走替换。
    """
    if not text:
        return text
    values = _injected.get()
    if not values:
        return text
    hits = 0
    for value in values:
        if value in text:
            text = text.replace(value, _mask(value))
            hits += 1
    if hits and log_hits:
        # 只记位置和条数，**不记值也不记命中内容**——日志本身就是泄漏面
        logger.warning(
            "[SecretRedact] %s 的结果里出现 %s 条本次注入的凭据值，已掩码",
            where or "工具",
            hits,
        )
    return text


def redact_value(value: Any, *, where: str = "", log_hits: bool = True) -> Any:
    """递归掩码。字典的 **key 不动**（key 是字段名，不是值）。"""
    if isinstance(value, str):
        return redact_text(value, where=where, log_hits=log_hits)
    if isinstance(value, Mapping):
        return {
            key: redact_value(item, where=where, log_hits=log_hits)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        masked = [redact_value(item, where=where, log_hits=log_hits) for item in value]
        return type(value)(masked) if isinstance(value, tuple) else masked
    return value


def looks_like_leak(payload: Any) -> bool:
    """结果里有没有出现本次注入的值。给诊断用，不做替换。"""
    values = _injected.get()
    if not values:
        return False
    text = payload if isinstance(payload, str) else str(payload)
    return any(value in text for value in values)
