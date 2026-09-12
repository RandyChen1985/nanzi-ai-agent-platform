"""AgentScope Middleware 扩展：LLM 调用统计与权限审计。

- ``ModelCallStatsMiddleware``：``on_model_call`` 记录 token / 工具 / 耗时到 Redis
- ``ToolPermissionMiddleware``：``on_check_permission`` 默认透传并审计日志
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Any, AsyncGenerator, Awaitable, Callable, Union

from agentscope.middleware import MiddlewareBase

from .context_breakdown import ModelInputTokenMemo, estimate_context_breakdown

logger = logging.getLogger(__name__)

STATS_KEY_SUFFIX = "model_call_stats"
STATS_TTL_SECONDS = 2592000  # 30 天


def _build_redis_key(user_id: str | int | None, conversation_id: str) -> str:
    from app.services.ai.memory_service import memory_service
    from app.services.ai.conversation_identity import require_user_id

    uid = require_user_id(user_id)
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


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    """安全地获取属性，防范 agentscope DictMixin 抛出 KeyError。"""
    try:
        return getattr(obj, name, default)
    except (AttributeError, KeyError):
        if isinstance(obj, dict):
            return obj.get(name, default)
        return default


def mark_model_fallback(agent: Any, current_model: Any) -> dict[str, str] | None:
    """Record the actual fallback model selected by AgentScope for this Agent."""
    primary_model = _safe_getattr(agent, "model")
    if primary_model is None or current_model is None:
        return None
    if current_model is primary_model:
        # AgentScope 可复用同一个 Agent；新一轮主模型调用开始时清掉上轮标记。
        try:
            setattr(agent, "_platform_fallback_info", None)
        except Exception:
            logger.warning("[ModelCallStatsMiddleware] Failed to clear fallback model info")
        return None

    existing = _safe_getattr(agent, "_platform_fallback_info")
    if isinstance(existing, dict):
        return existing

    info = {
        "primary_model": str(_safe_getattr(primary_model, "model", "unknown") or "unknown"),
        "fallback_model": str(_safe_getattr(current_model, "model", "unknown") or "unknown"),
    }
    logger.warning(
        "[ModelCallStatsMiddleware] AgentScope model fallback: primary=%s fallback=%s",
        info["primary_model"],
        info["fallback_model"],
    )
    try:
        setattr(agent, "_platform_fallback_info", info)
    except Exception:
        logger.warning(
            "[ModelCallStatsMiddleware] Failed to record fallback model info"
        )
    return info


async def _clamp_completion_to_context(
    current_model: Any,
    messages: list,
    tools: list,
    *,
    input_tokens: int | None = None,
) -> tuple[Any, Any, int | None]:
    """在实际模型调用前按精确 AgentScope 估算保护总上下文预算。

    平台的历史压缩使用的是独立的近似预算，系统提示和工具 schema 由 runner
    在之后注入，因此这里再用当前模型的 ``count_tokens`` 做最后一道保护。只在
    ``input + max_tokens`` 超过模型物理窗口时临时降低输出上限，并由调用方恢复
    原值，避免一次边界请求污染后续工具轮次。
    """
    if current_model is None:
        return None, None, None
    parameters = _safe_getattr(current_model, "parameters")
    requested = _safe_getattr(parameters, "max_tokens")
    context_size = _safe_getattr(current_model, "context_size")
    try:
        requested = int(requested or 0)
        context_size = int(context_size or 0)
    except (TypeError, ValueError):
        return parameters, None, None
    if requested <= 0 or context_size <= 0:
        return parameters, None, None

    if input_tokens is None:
        try:
            input_tokens = int(
                await current_model.count_tokens(messages=messages, tools=tools)
            )
        except Exception as exc:
            logger.warning(
                "[ModelCallStatsMiddleware] Failed to count model input for completion guard: %s",
                exc,
            )
            return parameters, None, None

    available = context_size - input_tokens
    if available <= 0 or requested <= available:
        return parameters, None, None
    try:
        parameters.max_tokens = available
    except Exception as exc:
        logger.warning(
            "[ModelCallStatsMiddleware] Failed to clamp max_tokens for model call: %s",
            exc,
        )
        return parameters, None, None
    logger.warning(
        "[ModelCallStatsMiddleware] Completion budget clamped: model=%s input=%d "
        "context=%d requested_output=%d effective_output=%d",
        _safe_getattr(current_model, "model", "unknown"),
        input_tokens,
        context_size,
        requested,
        available,
    )
    return parameters, requested, available


def _is_async_iterable(obj: Any) -> bool:
    return _safe_getattr(obj, "__aiter__") is not None


def _count_message_roles(messages: list) -> dict[str, int]:
    """统计 input 消息中每个角色的条数，便于可视化上下文构成。"""
    roles: dict[str, int] = {}
    try:
        for msg in messages or []:
            role = _safe_getattr(msg, "role", None) or "unknown"
            roles[role] = roles.get(role, 0) + 1
    except Exception:
        pass
    return roles


def _contains_compaction(messages: list) -> bool:
    """判断 input 消息中是否包含早前对话的裁剪摘录标记。"""
    if not messages:
        return False
    try:
        from app.services.ai.context_compaction import COMPACTION_MARKER
    except Exception:
        return False
    try:
        for msg in messages:
            content = _safe_getattr(msg, "content", None)
            blocks = content if isinstance(content, list) else [content]
            for block in blocks:
                text = (
                    block.get("text", "") if isinstance(block, dict)
                    else _safe_getattr(block, "text", "")
                )
                if text and COMPACTION_MARKER in text:
                    return True
    except Exception:
        pass
    return False


def _extract_tool_calls_detail(content: Any) -> list[dict[str, Any]]:
    """从 ChatResponse.content 中提取包含详细参数的工具调用列表。"""
    tool_calls: list[dict[str, Any]] = []
    try:
        for block in content or []:
            if _safe_getattr(block, "type", None) == "tool_call":
                name = _safe_getattr(block, "name", None)
                args = _safe_getattr(block, "arguments", None)
                if name:
                    arguments_val = args
                    if isinstance(args, str) and args.strip():
                        try:
                            arguments_val = json.loads(args)
                        except Exception:
                            pass
                    tool_calls.append({
                        "name": str(name),
                        "arguments": arguments_val or {}
                    })
    except Exception:
        pass
    return tool_calls


def _extract_usage_details(usage_obj: Any) -> tuple[int, int, int, str]:
    """从 AgentScope/OpenAI/Anthropic usage 提取 token 与归一化缓存命中。"""
    if usage_obj is None:
        return 0, 0, 0, "unavailable"
    in_tokens = int(
        _safe_getattr(usage_obj, "input_tokens", 0)
        or _safe_getattr(usage_obj, "prompt_tokens", 0)
        or 0
    )
    out_tokens = int(
        _safe_getattr(usage_obj, "output_tokens", 0)
        or _safe_getattr(usage_obj, "completion_tokens", 0)
        or 0
    )
    cache_tokens = 0
    source = "agentscope_usage"

    cached = _safe_getattr(usage_obj, "cache_input_tokens", None)
    if cached is not None:
        cache_tokens = int(cached)
    else:
        p_details = _safe_getattr(usage_obj, "prompt_tokens_details", None)
        if isinstance(p_details, dict) and "cached_tokens" in p_details:
            cache_tokens = int(p_details.get("cached_tokens") or 0)
            source = "openai_prompt_tokens_details"
        elif _safe_getattr(usage_obj, "cache_read_input_tokens", None) is not None:
            cache_tokens = int(_safe_getattr(usage_obj, "cache_read_input_tokens") or 0)
            source = "cache_read_input_tokens"
    return in_tokens, out_tokens, cache_tokens, source


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
    all_tool_calls: list[dict[str, Any]] = []
    has_tool_calls = False
    full_text_chunks: list[str] = []
    full_reasoning_chunks: list[str] = []

    last_complete_text = None
    last_complete_reasoning = None

    async for chunk in gen:
        _details = _extract_tool_calls_detail(_safe_getattr(chunk, "content", None))
        if _details:
            has_tool_calls = True
            for call in _details:
                if not any(c["name"] == call["name"] and c["arguments"] == call["arguments"] for c in all_tool_calls):
                    all_tool_calls.append(call)
                if call["name"] not in all_tool_names:
                    all_tool_names.append(call["name"])
        
        # 1. 提取当前 chunk 的 text 和 reasoning
        chunk_text = _safe_getattr(chunk, "text", "") or ""
        chunk_reasoning = _safe_getattr(chunk, "reasoning_content", "") or ""
        
        # 2. 从 chunk.content 解析 Block
        content_list = _safe_getattr(chunk, "content") or []
        block_text_list = []
        block_reasoning_list = []
        for block in content_list:
            b_type = _safe_getattr(block, "type")
            if b_type == "text":
                txt = _safe_getattr(block, "text", "")
                if txt:
                    block_text_list.append(txt)
            elif b_type == "thinking":
                thk = _safe_getattr(block, "thinking", "")
                if thk:
                    block_reasoning_list.append(thk)
        
        if not chunk_text and block_text_list:
            chunk_text = "".join(block_text_list)
        if not chunk_reasoning and block_reasoning_list:
            chunk_reasoning = "".join(block_reasoning_list)

        # 3. 判断是否是完整响应
        is_last = _safe_getattr(chunk, "is_last", False)
        if is_last:
            last_complete_text = chunk_text
            last_complete_reasoning = chunk_reasoning
        else:
            if chunk_text:
                full_text_chunks.append(chunk_text)
            if chunk_reasoning:
                full_reasoning_chunks.append(chunk_reasoning)

        chunk_usage = _safe_getattr(chunk, "usage", None)
        if chunk_usage is not None:
            last_usage = chunk_usage
        yield chunk

    # 流式结束后写入统计
    elapsed_ms = (time.time() - start_ts) * 1000
    final_text = last_complete_text if last_complete_text is not None else "".join(full_text_chunks)
    final_reasoning = last_complete_reasoning if last_complete_reasoning is not None else "".join(full_reasoning_chunks)

    in_tokens, out_tokens, cache_tokens, usage_source = _extract_usage_details(last_usage)
    record = {
        **record_base,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "cache_input_tokens": cache_tokens,
        "total_tokens": in_tokens + out_tokens,
        "usage_source": usage_source,
        "has_tool_calls": has_tool_calls,
        "tool_names": all_tool_names,
        "tool_calls": all_tool_calls,
        "response_text": final_text,
        "reasoning_content": final_reasoning,
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

    Redis Key: nanzi:{uid}:{conv_id}:model_call_stats
    存储结构: Redis List，每个元素为一条 JSON 调用记录。
    """

    def __init__(
        self,
        user_id: str | int | None,
        conversation_id: str,
        agent_name: str,
        trace_id: str | None = None,
        physical_window: int | None = None,
        history_budget: int | None = None,
        overhead_reservation: int | None = None,
        completion_reserve: int | None = None,
        request_input_budget: int | None = None,
        prompt_overhead_reservation: int | None = None,
        prompt_layout_mode: str | None = None,
    ) -> None:
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._agent_name = agent_name
        self._trace_id = trace_id
        self._physical_window = physical_window
        self._history_budget = history_budget
        self._overhead_reservation = overhead_reservation
        self._completion_reserve = completion_reserve
        self._request_input_budget = request_input_budget
        self._prompt_overhead_reservation = prompt_overhead_reservation
        self._prompt_layout_mode = prompt_layout_mode
        self._call_index = 0
        # 平台侧对话上下文预算（agent_context_max_tokens，默认 64k）的缓存与解析标记。
        # None 表示「尚未解析」，解析一次后缓存，避免每轮工具调用重复查配置。
        self._budget_context_size: int | None = None
        self._budget_is_resolved = False

    async def _resolve_context_budget(self) -> int | None:
        """解析平台侧对话上下文 Token 预算（agent_context_max_tokens，默认 64k）。

        该值即实际发送/截断 LLM 上下文的水位线：历史累计估算 token 超过它就从最早
        历史开始截断（必要时触发 compact）。结果缓存一次，避免每轮工具调用重复查配置。
        """
        if self._budget_is_resolved:
            return self._budget_context_size
        value: int | None = None
        try:
            from app.services.config_service import ConfigService

            raw = await ConfigService.get("agent_context_max_tokens", "65536")
            if raw is not None and str(raw).strip() != "":
                value = int(raw)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[ModelCallStatsMiddleware] Invalid agent_context_max_tokens: %s", exc
            )
        except Exception as exc:  # 配置读取失败不应阻断 LLM 调用
            logger.warning(
                "[ModelCallStatsMiddleware] Failed to read context budget: %s", exc
            )
        self._budget_context_size = value
        self._budget_is_resolved = True
        return value

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
        mark_model_fallback(agent, current_model)
        model_name: str = getattr(current_model, "model", "unknown")
        input_messages: list = input_kwargs.get("messages", [])
        input_message_count: int = len(input_messages)
        has_tools_bound: bool = bool(tools)

        # 模型物理上下文窗口大小（由 ai_model.context_size 经构造注入的实例属性）。
        # 若模型未配置 context_size，则用平台侧对话上下文预算（agent_context_max_tokens）
        # 兜底，避免分母落到 agentscope 硬编码默认值造成百分比失真。
        context_size: int | None = getattr(current_model, "context_size", None)
        # 平台侧历史截断水位线。默认读 agent_context_max_tokens（64k）兜底；
        # 当模型显式配置了更大的物理窗口（current_model.context_size 由构造注入，
        # 仅显式配置时才存在），则同步抬高水位线，避免提前 compact——与
        # agent_service._resolve_runtime_context_budget 的截断逻辑保持一致。
        context_budget: int | None = self._history_budget
        if not context_budget or context_budget <= 0:
            context_budget = await self._resolve_context_budget()
        physical_window = self._physical_window
        if not physical_window or physical_window <= 0:
            physical_window = context_size if context_size and context_size > 0 else context_budget
        if not context_size or context_size <= 0:
            context_size = physical_window
        # 各角色的消息条数统计（便于前端可视化上下文构成），以及是否包含早前对话的裁剪摘录。
        message_roles: dict[str, int] = _count_message_roles(input_messages)
        contains_compaction: bool = _contains_compaction(input_messages)
        token_memo = ModelInputTokenMemo()
        context_breakdown = await estimate_context_breakdown(
            current_model,
            input_messages,
            tools,
            token_memo=token_memo,
        )

        parameters, original_max_tokens, effective_max_tokens = (
            await _clamp_completion_to_context(
                current_model,
                input_messages,
                tools,
                input_tokens=token_memo.total_tokens,
            )
        )
        try:
            result = await next_handler(**input_kwargs)
        finally:
            if original_max_tokens is not None and parameters is not None:
                try:
                    parameters.max_tokens = original_max_tokens
                except Exception:
                    logger.warning(
                        "[ModelCallStatsMiddleware] Failed to restore max_tokens "
                        "after guarded model call"
                    )

        raw_model_output = _safe_getattr(
            _safe_getattr(current_model, "parameters"), "max_tokens"
        )
        try:
            model_completion_reserve = int(raw_model_output or 0)
        except (TypeError, ValueError):
            model_completion_reserve = 0
        if model_completion_reserve <= 0:
            try:
                model_completion_reserve = int(self._completion_reserve or 0)
            except (TypeError, ValueError):
                model_completion_reserve = 0

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
            "context_size": context_size,
            "context_budget": context_budget,
            "physical_window": physical_window,
            "history_budget": context_budget,
            "completion_reserve_tokens": max(0, model_completion_reserve or 0),
            "request_input_budget": self._request_input_budget,
            "prompt_overhead_reservation_tokens": self._prompt_overhead_reservation,
            "effective_completion_limit": effective_max_tokens,
            "overhead_reservation_tokens": (
                self._overhead_reservation
                if self._overhead_reservation is not None
                else max(0, (physical_window or 0) - (context_budget or 0))
            ),
            "message_roles": message_roles,
            "contains_compaction": contains_compaction,
            "context_breakdown": context_breakdown,
            "prompt_layout_mode": self._prompt_layout_mode,
        }

        # ── 流式响应：return 包装后的 async generator ──────────────────────
        if _is_async_iterable(result):
            return _stream_with_stats(
                result,
                redis_key=redis_key,
                record_base=record_base,
                start_ts=start_ts,
            )

        # ── 非流式响应：直接收集并写入 ────────────────────────────────────
        elapsed_ms = (time.time() - start_ts) * 1000
        usage = _safe_getattr(result, "usage", None)
        tool_calls = _extract_tool_calls_detail(_safe_getattr(result, "content", None))
        has_tool_calls = bool(tool_calls)
        tool_names = [c["name"] for c in tool_calls]
        input_tokens, output_tokens, cache_input_tokens, usage_source = _extract_usage_details(usage)
        
        response_text = _safe_getattr(result, "text", "") or ""
        reasoning_content = _safe_getattr(result, "reasoning_content", "") or ""
        
        content_list = _safe_getattr(result, "content") or []
        block_text_list = []
        block_reasoning_list = []
        for block in content_list:
            b_type = _safe_getattr(block, "type")
            if b_type == "text":
                txt = _safe_getattr(block, "text", "")
                if txt:
                    block_text_list.append(txt)
            elif b_type == "thinking":
                thk = _safe_getattr(block, "thinking", "")
                if thk:
                    block_reasoning_list.append(thk)
        
        if not response_text and block_text_list:
            response_text = "".join(block_text_list)
        if not reasoning_content and block_reasoning_list:
            reasoning_content = "".join(block_reasoning_list)

        record = {
            **record_base,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_input_tokens": cache_input_tokens,
            "total_tokens": input_tokens + output_tokens,
            "usage_source": usage_source,
            "has_tool_calls": has_tool_calls,
            "tool_names": tool_names,
            "tool_calls": tool_calls,
            "response_text": response_text,
            "reasoning_content": reasoning_content,
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


class ToolPermissionMiddleware(MiddlewareBase):
    """AgentScope ``on_check_permission`` 审计切面。

    默认**始终透传** ``next_handler`` 的决策，不改变现有
    ``tools.check_permissions`` / HITL / forbidden_tools 行为。
    可选 ``deny_override`` 仅允许把结果收紧为 DENY（永不放宽）。
    """

    def __init__(
        self,
        *,
        user_id: str | int | None = None,
        conversation_id: str | None = None,
        agent_name: str | None = None,
        deny_override: Callable[..., Awaitable[Any]] | Callable[..., Any] | None = None,
    ) -> None:
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._agent_name = agent_name
        self._deny_override = deny_override

    async def on_check_permission(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        decision = await next_handler(**input_kwargs)
        tool = input_kwargs.get("tool")
        tool_name = getattr(tool, "name", None) or getattr(
            input_kwargs.get("tool_call"), "name", None
        )
        behavior = getattr(decision, "behavior", None)
        behavior_value = getattr(behavior, "value", behavior)

        logger.info(
            "[ToolPermissionMiddleware] agent=%s conv=%s user=%s tool=%s behavior=%s",
            self._agent_name or getattr(agent, "name", None),
            self._conversation_id,
            self._user_id,
            tool_name,
            behavior_value,
        )

        if self._deny_override is None:
            return decision

        try:
            override = self._deny_override(
                agent=agent,
                input_kwargs=input_kwargs,
                decision=decision,
            )
            if asyncio.iscoroutine(override):
                override = await override
        except Exception as exc:
            logger.warning(
                "[ToolPermissionMiddleware] deny_override failed tool=%s: %s",
                tool_name,
                exc,
            )
            return decision

        if override is None:
            return decision

        from agentscope.permission import PermissionBehavior

        override_behavior = getattr(override, "behavior", None)
        if override_behavior == PermissionBehavior.DENY:
            logger.info(
                "[ToolPermissionMiddleware] deny_override applied tool=%s reason=%s",
                tool_name,
                getattr(override, "decision_reason", None)
                or getattr(override, "message", None),
            )
            return override
        logger.warning(
            "[ToolPermissionMiddleware] ignore non-DENY override tool=%s behavior=%s",
            tool_name,
            getattr(override_behavior, "value", override_behavior),
        )
        return decision


class BashSandboxParentLinkMiddleware(MiddlewareBase):
    """把「本次 Bash 触发」绑定到 Bash 卡片节点 id 的切面。

    AgentScope ``on_check_permission`` 在工具真正执行（``_acting``）之前触发，
    且与工具执行处于同一条 ``_execute_tool_call`` 协程链上，此处写入的 ContextVar
    能可靠传播到稍后原生 Bash 工具调 ``ensure_ready`` 的位置。

    仅当目标是惰性沙箱 Bash 工具（工具名 "Bash"）时写入节点 id，其余工具保持透传；读取方（
    ``LazySandboxBashNativeTool``）也只在该 Bash 路径上消费，因此文件工具、agent
    构建预检等触发 ensure_ready 的路径不受影响。
    """

    @staticmethod
    def _attr(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _is_bash_tool(self, tool: Any, tool_call: Any) -> bool:
        # 工具封装（AgentScopeNativeApprovalTool）name 为 "Bash"；原生代理可依附例识别。
        name = self._attr(tool, "name") or self._attr(tool_call, "name")
        if str(name) == "Bash":
            return True
        try:
            from app.services.ai.runtime.agentscope.workspace import (
                LazySandboxBashNativeTool,
            )

            return isinstance(tool, LazySandboxBashNativeTool)
        except Exception:
            return False

    async def on_check_permission(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Awaitable[Any]],
    ) -> Any:
        tool = input_kwargs.get("tool")
        tool_call = input_kwargs.get("tool_call")
        is_bash = self._is_bash_tool(tool, tool_call)
        if is_bash:
            node_id = str(self._attr(tool_call, "id") or "")
            if node_id:
                from app.services.ai.runtime.agentscope.workspace import (
                    current_bash_tool_parent_id,
                )

                # 注意：工具真正执行（_acting / ensure_ready）发生在 on_check_permission
                # 返回之后，因此此处**不能**在 next_handler 之后 reset（会过早清空）。
                # 读取方仅限 Bash 路径且每次进入前都会重新 set，残留值不会污染其他路径。
                current_bash_tool_parent_id.set(node_id)
        return await next_handler(**input_kwargs)
