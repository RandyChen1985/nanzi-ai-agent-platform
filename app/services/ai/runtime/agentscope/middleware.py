"""AgentScope Middleware 扩展：LLM 调用统计。

实现 on_model_call 钩子，记录每次 LLM 调用的：
- 调用序号、时间戳、模型名
- 输入/输出 Token 数（含缓存命中 Token）
- 是否含工具调用及工具名列表
- 调用耗时（ms）

统计数据以追加方式写入 Redis List，Key 格式：
    yunshu:{uid}:{conv_id}:model_call_stats
TTL：7 天（与 AgentState 保持一致）
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Any, AsyncGenerator, Awaitable, Callable, Union

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)

STATS_KEY_SUFFIX = "model_call_stats"
STATS_TTL_SECONDS = 604800  # 7 天


def _build_redis_key(user_id: str | int | None, conversation_id: str) -> str:
    from app.services.ai.memory_service import memory_service

    uid = str(user_id) if user_id is not None else "anonymous"
    return f"{memory_service.KEY_PREFIX}:{uid}:{conversation_id}:{STATS_KEY_SUFFIX}"


async def _append_stat_to_redis(key: str, record: dict[str, Any]) -> None:
    """将单条统计记录追加到 Redis List 末尾（fire-and-forget）。"""
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if not redis:
            return
        await redis.rpush(key, json.dumps(record, ensure_ascii=False))
        await redis.expire(key, STATS_TTL_SECONDS)
    except Exception as exc:
        logger.warning("[ModelCallStatsMiddleware] Redis append failed: %s", exc)


def _extract_tool_info(content: Any) -> tuple[bool, list[str]]:
    """从 ChatResponse.content 中提取工具调用信息。"""
    has_tool_calls = False
    tool_names: list[str] = []
    try:
        for block in content or []:
            if getattr(block, "type", None) == "tool_call":
                has_tool_calls = True
                name = getattr(block, "name", None)
                if name:
                    tool_names.append(str(name))
    except Exception:
        pass
    return has_tool_calls, tool_names


async def _stream_with_stats(
    gen: AsyncGenerator,
    *,
    redis_key: str,
    record_base: dict[str, Any],
    start_ts: float,
) -> AsyncGenerator:
    """包装流式 generator，在所有 chunk 消费完后追加统计记录。"""
    last_usage = None
    all_tool_names: list[str] = []
    has_tool_calls = False

    async for chunk in gen:
        _has_tc, _names = _extract_tool_info(getattr(chunk, "content", None))
        if _has_tc:
            has_tool_calls = True
            for n in _names:
                if n not in all_tool_names:
                    all_tool_names.append(n)
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            last_usage = chunk_usage
        yield chunk

    # 流式结束后写入统计
    elapsed_ms = (time.time() - start_ts) * 1000
    record = {
        **record_base,
        "input_tokens": getattr(last_usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(last_usage, "output_tokens", 0) or 0,
        "cache_input_tokens": getattr(last_usage, "cache_input_tokens", 0) or 0,
        "total_tokens": (
            (getattr(last_usage, "input_tokens", 0) or 0)
            + (getattr(last_usage, "output_tokens", 0) or 0)
        ),
        "has_tool_calls": has_tool_calls,
        "tool_names": all_tool_names,
        "elapsed_ms": round(elapsed_ms, 1),
    }
    logger.info(
        "[ModelCallStats] conv=%s agent=%s call#%d model=%s "
        "in=%d out=%d cache_in=%d tools=%s elapsed=%.1fms",
        record_base.get("conversation_id", ""),
        record_base.get("agent_name", ""),
        record_base.get("call_index", 0),
        record_base.get("model_name", ""),
        record["input_tokens"],
        record["output_tokens"],
        record["cache_input_tokens"],
        all_tool_names or False,
        elapsed_ms,
    )
    asyncio.ensure_future(_append_stat_to_redis(redis_key, record))


class ModelCallStatsMiddleware(MiddlewareBase):
    """统计每次 LLM 调用的 Token 消耗与工具调用情况，写入 Redis。

    Redis Key: yunshu:{uid}:{conv_id}:model_call_stats
    存储结构: Redis List，每个元素为一条 JSON 调用记录。
    """

    def __init__(
        self,
        user_id: str | int | None,
        conversation_id: str,
        agent_name: str,
        trace_id: str | None = None,
    ) -> None:
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._agent_name = agent_name
        self._trace_id = trace_id
        self._call_index = 0

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable[Any],
        ],
    ) -> Union[Any, AsyncGenerator]:
        self._call_index += 1
        call_index = self._call_index
        start_ts = time.time()

        tools: list = input_kwargs.get("tools", [])
        current_model = input_kwargs.get("current_model")
        model_name: str = getattr(current_model, "model", "unknown")
        input_message_count: int = len(input_kwargs.get("messages", []))
        has_tools_bound: bool = bool(tools)

        result = await next_handler(**input_kwargs)

        redis_key = _build_redis_key(self._user_id, self._conversation_id)
        record_base = {
            "call_index": call_index,
            "timestamp": datetime.datetime.fromtimestamp(
                start_ts, tz=datetime.timezone.utc
            ).isoformat(),
            "conversation_id": self._conversation_id,
            "agent_name": self._agent_name,
            "model_name": model_name,
            "input_message_count": input_message_count,
            "has_tools_bound": has_tools_bound,
            "trace_id": self._trace_id,
        }

        # ── 流式响应：return 包装后的 async generator ──────────────────────
        if hasattr(result, "__aiter__"):
            return _stream_with_stats(
                result,
                redis_key=redis_key,
                record_base=record_base,
                start_ts=start_ts,
            )

        # ── 非流式响应：直接收集并写入 ────────────────────────────────────
        elapsed_ms = (time.time() - start_ts) * 1000
        usage = getattr(result, "usage", None)
        has_tool_calls, tool_names = _extract_tool_info(
            getattr(result, "content", None)
        )
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_input_tokens = getattr(usage, "cache_input_tokens", 0) or 0

        record = {
            **record_base,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_input_tokens": cache_input_tokens,
            "total_tokens": input_tokens + output_tokens,
            "has_tool_calls": has_tool_calls,
            "tool_names": tool_names,
            "elapsed_ms": round(elapsed_ms, 1),
        }
        logger.info(
            "[ModelCallStats] conv=%s agent=%s call#%d model=%s "
            "in=%d out=%d cache_in=%d tools=%s elapsed=%.1fms",
            self._conversation_id,
            self._agent_name,
            call_index,
            model_name,
            input_tokens,
            output_tokens,
            cache_input_tokens,
            tool_names or False,
            elapsed_ms,
        )
        asyncio.ensure_future(_append_stat_to_redis(redis_key, record))
        return result
