"""ContextCompactor: 负责多轮会话上下文预算控制、窗口裁剪、溢出压缩与语义摘要生成。"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from app.core.config import settings
from app.schemas.agent import ChatConfig
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.config import RuntimeModelInfo
from app.services.ai.context_compaction_log_service import context_compaction_log_service
from app.services.ai.memory_service import memory_service, MemoryService
from app.services.ai.runtime.agentscope.tool_result_context import is_trusted_tool_result_context
from app.services.config_service import ConfigService
from app.services.schema_chunk_format import estimate_text_tokens

logger = logging.getLogger(__name__)

_LLM_DIGEST_TASKS: Set[asyncio.Task] = set()


def trusted_tool_run_text(message: Dict[str, Any]) -> str:
    """只为上下文预算统计读取已标记版本的最终工具结果。"""
    if not is_trusted_tool_result_context(message):
        return ""
    return str(message.get("tool_run_text") or "")


def history_messages_for_token_budget(
    history: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """剔除被打断/被取消的未完成轮次，保留正常完成的历史记录。

    ⚠️ 注意：此函数（Token 预算用，字段原样保留）与 agent_service.py 中的
    history_messages_for_llm（进模型用，做 allowed_keys 字段过滤 + cancelled/interrupted
    双阶段清理）语义不同，请勿合并后丢弃 allowed_keys 字段过滤，否则会导致字段泄漏。
    二者在「剔除断轮（interrupted/cancelled）」上保持一致：cancelled 半截轮绝不进入
    token 预算窗口或最终发给 executor 的上下文。
    """
    cleaned: List[Dict[str, Any]] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        status = str(msg.get("status") or "").strip().lower()
        if status in ("interrupted", "cancelled"):
            continue
        cleaned.append(msg)
    return cleaned


def window_for_context(
    server_history: List[Dict[str, Any]],
    max_context_messages: int,
    max_tokens: int,
) -> List[Dict[str, Any]]:
    """用 token 预算主导窗口，条数上限仅作绝对兜底。"""
    if not server_history:
        return []
    total_tokens = 0
    kept = 0
    cut_index: Optional[int] = None
    for idx in range(len(server_history) - 1, -1, -1):
        msg = server_history[idx]
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "")
        tool_text = trusted_tool_run_text(msg)
        total_tokens += estimate_text_tokens(content + tool_text)
        kept += 1
        if total_tokens > max_tokens:
            cut_index = idx
            break
        if kept > max_context_messages:
            cut_index = idx
            break
    if cut_index is None:
        return server_history
    # 绝不返回空窗口：当最新一条消息本身已超预算（cut_index == len-1）时，
    # server_history[cut_index+1:] 会得到 []，导致全部历史（含最新一条）被清空、
    # 下游只收到孤立的 user 消息。此时至少保留最新这一条最相关的上下文。
    if cut_index >= len(server_history) - 1:
        return server_history[-1:]
    return server_history[cut_index + 1 :]


async def apply_context_snapshot(
    history: List[Dict[str, Any]],
    *,
    user_id: str,
    conversation_id: str,
) -> List[Dict[str, Any]]:
    """将手动压缩快照与快照创建后的新消息合并，原始历史保持不变。"""
    snapshot = await memory_service.get_context_snapshot(user_id, conversation_id)
    if not isinstance(snapshot, dict):
        return history
    return memory_service.merge_context_snapshot(history, snapshot)


class ContextCompactor:
    """Encapsulates context compaction, pruning, LLM digest generation and persistence."""

    @staticmethod
    async def persist_context_compaction_event(
        event: Dict[str, Any],
        *,
        user_id: Any,
        conversation_id: Optional[str],
        trace_id: Optional[str],
        source: str,
        stage: str,
        agent_name: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> None:
        """尽力记录压缩事件；Redis 故障不能影响当前 SSE 主链路。"""
        if not user_id or not conversation_id or not event:
            return
        try:
            await asyncio.wait_for(
                context_compaction_log_service.append_event(
                    event=event,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    trace_id=trace_id,
                    source=source,
                    stage=stage,
                    agent_name=agent_name,
                    model_name=model_name,
                ),
                timeout=context_compaction_log_service.APPEND_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "[ContextCompactor] Failed to persist context compaction event",
                exc_info=True,
            )

    @classmethod
    async def rebuild_context_for_resolved_model(
        cls,
        agent_service: Any,
        *,
        messages: List[Dict[str, Any]],
        runtime_model_info: RuntimeModelInfo,
        conversation_id: Optional[str],
        user_info: Optional[Dict[str, Any]],
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
        shared_state: Optional[Dict[str, Any]],
        synthesis_runtime_model_info: Optional[RuntimeModelInfo] = None,
    ) -> List[Dict[str, Any]]:
        """路由完成后按目标模型重新构造真正发送给 executor 的上下文。"""
        if not conversation_id or not shared_state:
            return messages
        source_history = shared_state.get("context_source_history")
        if not isinstance(source_history, list):
            return messages

        target_history_budget = await agent_service._history_budget_for_runtime_model_info(
            runtime_model_info
        )
        if synthesis_runtime_model_info is not None:
            target_history_budget = min(
                target_history_budget,
                await agent_service._history_budget_for_runtime_model_info(
                    synthesis_runtime_model_info
                ),
            )
        model_key = ":".join(
            filter(
                None,
                (
                    getattr(runtime_model_info, "effective_model_id", None),
                    (
                        getattr(synthesis_runtime_model_info, "effective_model_id", None)
                        if synthesis_runtime_model_info is not None
                        else None
                    ),
                ),
            )
        )
        if (
            shared_state.get("context_finalized_model") == model_key
            and shared_state.get("context_history_budget") == target_history_budget
        ):
            return messages

        max_context_raw = await ConfigService.get("agent_max_context_messages", "60")
        try:
            max_context = int(max_context_raw)
        except (TypeError, ValueError):
            max_context = 60
        user_message = shared_state.get("context_user_message")
        window = history_messages_for_token_budget(
            window_for_context(
                source_history,
                max_context,
                target_history_budget,
            )
        )
        candidate_windows = [
            value
            for value in (
                agent_service._configured_model_window(runtime_model_info),
                agent_service._configured_model_window(synthesis_runtime_model_info)
                if synthesis_runtime_model_info is not None
                else 0,
            )
            if value > 0
        ]
        physical_window = (
            min(candidate_windows)
            if candidate_windows
            else await agent_service._resolve_pre_route_context_budget()
        )
        completion_reserve = max(
            agent_service._configured_model_output(runtime_model_info),
            agent_service._configured_model_output(synthesis_runtime_model_info)
            if synthesis_runtime_model_info is not None
            else 0,
        )
        final_ctx_event: dict = {}
        full_for_compact = history_messages_for_token_budget(source_history)
        compact_kwargs: dict = dict(
            user_id=(user_info or {}).get("user_id"),
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            token_budget=target_history_budget,
            enable_llm_summary=True,
            out=final_ctx_event,
            physical_window=physical_window,
            completion_reserve_tokens=completion_reserve,
        )
        # 优先走实例方法缝：保住调用方对 _maybe_compact_overflow 的注入/覆写/单测 affordance，
        # 生产上 agent_service._maybe_compact_overflow 会再委托回 cls.maybe_compact_overflow，行为等
        # 价；仅当未通过 agent_service 调用（如脱离实例直接使用 ContextCompactor）时才落到静态实现。
        if agent_service is not None and hasattr(agent_service, "_maybe_compact_overflow"):
            # full_history/window 作为位置参数传入，与既有调用形态（及依赖 kwargs 快照的测试）保持一致。
            compacted = await agent_service._maybe_compact_overflow(
                full_for_compact, window, **compact_kwargs
            )
        else:
            compacted = await cls.maybe_compact_overflow(
                full_for_compact, window, **compact_kwargs
            )
        shared_state["context_finalized_model"] = model_key
        shared_state["context_history_budget"] = target_history_budget
        if final_ctx_event:
            final_ctx_event = dict(final_ctx_event)
            final_ctx_event["type"] = "context_summarized"
            shared_state["context_final_compaction_event"] = final_ctx_event
        if isinstance(user_message, dict) and user_message.get("role") == "user":
            return compacted + [user_message]
        return compacted

    @classmethod
    async def maybe_compact_overflow(
        cls,
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        *,
        agent_service: Optional[Any] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        out: Optional[dict] = None,
        token_budget: Optional[int] = None,
        enable_llm_summary: bool = True,
        physical_window: Optional[int] = None,
        completion_reserve_tokens: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """超出上下文窗口时，把被丢弃的旧消息压缩成一条 system 摘录注入窗口最前。"""
        if not full_history or len(full_history) <= len(window):
            return window
        try:
            from app.services.config_service import ConfigService

            enabled_raw = await ConfigService.get("agent_context_compaction_enabled", "true")
            if str(enabled_raw or "").strip().lower() not in {"1", "true", "yes", "on"}:
                return window
            max_chars_raw = await ConfigService.get("agent_context_compaction_max_chars", "1200")
            try:
                max_chars = max(200, int(max_chars_raw))
            except (TypeError, ValueError):
                max_chars = 1200

            prev_digest = None
            context_revision: Optional[int] = None
            source_seq = max(
                (
                    int(message.get("seq") or 0)
                    for message in full_history
                    if isinstance(message, dict)
                ),
                default=0,
            )
            if user_id and conversation_id:
                memory = MemoryService()
                try:
                    prev_digest = await memory.get_digest(user_id, conversation_id)
                except Exception as exc:
                    logger.warning("[Compaction] Failed to read persisted digest: %s", exc)
                    prev_digest = None
                try:
                    context_revision = await memory.get_context_revision(
                        user_id, conversation_id
                    )
                except Exception as exc:
                    logger.warning("[Compaction] Failed to read context revision: %s", exc)
                    context_revision = None
                try:
                    source_seq = max(
                        source_seq,
                        await memory.get_current_seq(user_id, conversation_id),
                    )
                except Exception as exc:
                    logger.warning("[Compaction] Failed to read current seq: %s", exc)

            from app.services.ai.context_compaction import (
                COMPACTION_MARKER,
                apply_context_compaction,
            )

            compacted = apply_context_compaction(
                full_history=full_history,
                window=window,
                max_chars=max_chars,
                prev_digest=prev_digest,
            )
            if len(compacted) == len(window):
                return compacted

            digest_origin = "deterministic"
            if enable_llm_summary and user_id and conversation_id:
                if agent_service and hasattr(agent_service, "_spawn_llm_digest_task"):
                    agent_service._spawn_llm_digest_task(
                        full_history,
                        window,
                        max_chars=max_chars,
                        prev_digest=prev_digest,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                        source_seq=source_seq,
                        source_revision=context_revision,
                    )
                else:
                    cls.spawn_llm_digest_task(
                        full_history,
                        window,
                        agent_service=agent_service,
                        max_chars=max_chars,
                        prev_digest=prev_digest,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                        source_seq=source_seq,
                        source_revision=context_revision,
                    )

            logger.info(
                "[Compaction] Injected overflow digest: dropped=%d kept=%d origin=%s",
                len(full_history) - len(window),
                len(window),
                digest_origin,
            )
            if out is not None and isinstance(compacted, list):
                cls.emit_compaction_card(
                    out,
                    compacted,
                    full_history,
                    window,
                    digest_origin,
                    token_budget,
                    physical_window,
                    completion_reserve_tokens,
                )
            if user_id and conversation_id:
                try:
                    digest_content = None
                    head = compacted[0]
                    if (
                        isinstance(head, dict)
                        and head.get("role") == "system"
                        and COMPACTION_MARKER in str(head.get("content", ""))
                    ):
                        digest_content = str(head.get("content"))
                    await MemoryService().set_digest_if_current(
                        user_id,
                        conversation_id,
                        digest_content or "",
                        source_seq=source_seq,
                        source_revision=context_revision,
                        quality=0,
                        allow_newer_seq=True,
                    )
                except Exception as exc:
                    logger.warning("[Compaction] Failed to persist digest: %s", exc)
            return compacted
        except Exception as exc:
            logger.warning("[Compaction] Failed to compact overflow history: %s", exc)
            return window

    @classmethod
    async def manual_compact_conversation(
        cls,
        agent_service: Any,
        user_id: str,
        conversation_id: str,
        *,
        retain_ratio: float = 0.5,
        mode: str = "fast",
    ) -> Dict[str, Any]:
        """由用户显式触发上下文压缩；默认快速模式，不改写原始历史。"""
        retain_ratio = float(retain_ratio)
        if retain_ratio not in {0.25, 0.5, 0.75}:
            raise ValueError("retain_ratio 只能是 0.25、0.5 或 0.75")
        if mode not in {"fast", "smart"}:
            raise ValueError("mode 只能是 fast 或 smart")
        history = await memory_service.get_history(user_id, conversation_id)
        history = await apply_context_snapshot(
            history or [], user_id=user_id, conversation_id=conversation_id
        )
        full_history = history_messages_for_token_budget(history or [])
        if len(full_history) < 2:
            return {
                "compacted": False,
                "reason": "history_too_short",
                "dropped": 0,
                "kept": len(full_history),
            }

        max_context_raw = await ConfigService.get("agent_max_context_messages", "60")
        try:
            max_context = max(1, int(max_context_raw))
        except (TypeError, ValueError):
            max_context = 60
        runtime_budget = await agent_service._resolve_pre_route_context_budget()
        history_budget = await agent_service._resolve_history_context_budget(runtime_budget)
        keep_count = max(1, int(len(full_history) * retain_ratio))
        automatic_window = window_for_context(full_history, max_context, history_budget)
        window = full_history[-keep_count:]
        if len(automatic_window) < len(window):
            window = automatic_window

        before_tokens = sum(
            estimate_text_tokens(
                str(message.get("content") or "") + trusted_tool_run_text(message)
            )
            for message in full_history
        )

        event: Dict[str, Any] = {}
        compacted = await cls.maybe_compact_overflow(
            full_history,
            window,
            user_id=user_id,
            conversation_id=conversation_id,
            out=event,
            token_budget=history_budget,
            enable_llm_summary=False,
            physical_window=runtime_budget,
        )
        if not event:
            return {
                "compacted": False,
                "reason": "no_compactable_history",
                "dropped": 0,
                "kept": len(full_history),
            }

        smart_digest = None
        if mode == "smart":
            smart_digest = await cls.try_llm_overflow_digest(
                full_history,
                window,
                prev_digest=None,
            )
            if smart_digest and smart_digest.get("content"):
                compacted = [smart_digest, *window]
                event["origin"] = "llm"

        source_seq = max((int(message.get("seq") or 0) for message in history), default=0)
        if smart_digest and smart_digest.get("content"):
            # 让 digest 键与快照头一致：smart 摘要必须同样持久化，否则后续轮
            # apply_context_snapshot 读到的是 smart digest 头，而 get_digest 仍返回
            # 确定性摘录，造成“双摘要锚点对碰”。quality 高于 maybe_compact_overflow
            # 内确定性 digest 的 quality=0，故能以相同 source_seq 覆盖之。
            try:
                await MemoryService().set_digest_if_current(
                    user_id,
                    conversation_id,
                    str(smart_digest.get("content") or ""),
                    source_seq=source_seq,
                    quality=1,
                    allow_newer_seq=True,
                )
            except Exception as exc:
                logger.warning(
                    "[Compaction] Failed to persist smart digest: %s", exc
                )
        await memory_service.set_context_snapshot(
            user_id,
            conversation_id,
            {"schema_version": 1, "source_seq": source_seq, "messages": compacted},
        )
        after_tokens = sum(
            estimate_text_tokens(
                str(message.get("content") or "") + trusted_tool_run_text(message)
            )
            for message in compacted
        )
        event = dict(event)
        event["type"] = "context_summarized"
        event["saved_tokens"] = max(0, before_tokens - after_tokens)
        event["saved_percent"] = (
            round(max(0, before_tokens - after_tokens) / before_tokens * 100, 1)
            if before_tokens
            else 0
        )
        await cls.persist_context_compaction_event(
            event,
            user_id=user_id,
            conversation_id=conversation_id,
            trace_id=f"manual-{uuid.uuid4().hex}",
            source="platform",
            stage="manual",
        )
        return {
            "compacted": True,
            "dropped": int(event.get("dropped") or 0),
            "kept": int(event.get("kept") or len(window)),
            "origin": event.get("origin") or "deterministic",
            "preview": event.get("preview") or "",
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": max(0, before_tokens - after_tokens),
            "saved_percent": (
                round(max(0, before_tokens - after_tokens) / before_tokens * 100, 1)
                if before_tokens
                else 0
            ),
            "retain_ratio": retain_ratio,
            "mode": mode,
        }

    @staticmethod
    def emit_compaction_card(
        out: dict,
        compacted: List[Dict[str, Any]],
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        origin: str,
        token_budget: Optional[int] = None,
        physical_window: Optional[int] = None,
        completion_reserve_tokens: Optional[int] = None,
    ) -> None:
        """把真实溢出压缩的观测信息写入 out 容器，供 SSE 生成器发射卡片。"""
        try:
            from app.services.ai.context_compaction import _extract_digest_body

            dropped = len(full_history) - len(window)
            kept = len(window)
            preview = ""
            head = compacted[0] if compacted else None
            if isinstance(head, dict) and head.get("role") == "system":
                preview = _extract_digest_body(str(head.get("content") or ""))
            if not preview:
                preview = str(head.get("content") or "") if isinstance(head, dict) else ""
            preview = preview.strip()
            if len(preview) > 300:
                preview = preview[:300].rstrip() + "……"
            out["dropped"] = dropped
            out["kept"] = kept
            out["origin"] = origin
            out["preview"] = preview
            out["title"] = "对话上下文已压缩（平台摘录）"
            try:
                token_used = sum(
                    estimate_text_tokens(
                        str(msg.get("content") or "") + trusted_tool_run_text(msg)
                    )
                    for msg in full_history
                    if isinstance(msg, dict)
                )
            except Exception:
                token_used = 0
            out["token_used"] = int(token_used)
            if isinstance(token_budget, int) and token_budget > 0:
                out["token_budget"] = token_budget
            else:
                out["token_budget"] = None
            out["history_budget"] = out["token_budget"]
            out["physical_window"] = (
                physical_window
                if isinstance(physical_window, int) and physical_window > 0
                else None
            )
            out["completion_reserve_tokens"] = (
                completion_reserve_tokens
                if isinstance(completion_reserve_tokens, int)
                and completion_reserve_tokens > 0
                else 0
            )
            if out["physical_window"] is not None and out["history_budget"] is not None:
                out["overhead_reservation_tokens"] = max(
                    0,
                    out["physical_window"] - out["history_budget"],
                )
            else:
                out["overhead_reservation_tokens"] = None
        except Exception as exc:
            logger.warning("[Compaction] Failed to build compaction card: %s", exc)

    @classmethod
    def spawn_llm_digest_task(
        cls,
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        *,
        agent_service: Optional[Any] = None,
        max_chars: int = 1200,
        prev_digest: Optional[str] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        source_seq: int = 0,
        source_revision: Optional[int] = None,
    ) -> Optional[asyncio.Task]:
        """后台生成 LLM 语义摘要，替代同步阻塞首 token。"""
        if not user_id or not conversation_id:
            return None

        async def _run() -> None:
            try:
                if agent_service and hasattr(agent_service, "_try_llm_overflow_digest"):
                    llm_digest = await agent_service._try_llm_overflow_digest(
                        full_history,
                        window,
                        max_chars=max_chars,
                        prev_digest=prev_digest,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                    )
                else:
                    llm_digest = await cls.try_llm_overflow_digest(
                        full_history,
                        window,
                        max_chars=max_chars,
                        prev_digest=prev_digest,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                    )
                if not llm_digest or not isinstance(llm_digest, dict):
                    return
                content = llm_digest.get("content")
                if not content:
                    return

                write_kwargs = {"source_seq": source_seq}
                write_kwargs["quality"] = 1
                write_kwargs["allow_newer_seq"] = True
                if source_revision is not None:
                    write_kwargs["source_revision"] = source_revision
                written = await MemoryService().set_digest_if_current(
                    user_id,
                    conversation_id,
                    str(content),
                    **write_kwargs,
                )
                if not written:
                    logger.info(
                        "[Compaction] Async LLM digest skipped for stale conversation=%s",
                        conversation_id,
                    )
                    return
                logger.info(
                    "[Compaction] Async LLM digest persisted for conversation=%s",
                    conversation_id,
                )
            except Exception as exc:
                logger.warning("[Compaction] Async LLM digest task failed: %s", exc)

        task = asyncio.get_running_loop().create_task(_run())
        _LLM_DIGEST_TASKS.add(task)
        task.add_done_callback(_LLM_DIGEST_TASKS.discard)
        return task

    @classmethod
    async def try_llm_overflow_digest(
        cls,
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
        *,
        max_chars: int = 1200,
        prev_digest: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
    ) -> Optional[Dict[str, str]]:
        """尝试用当前会话模型生成语义摘要，替代确定性拼装摘录。"""
        try:
            llm_summary_raw = await ConfigService.get(
                "agent_context_llm_summary_enabled", "true"
            )
            if str(llm_summary_raw or "").strip().lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                return None

            from app.services.ai.context_compaction import (
                COMPACTION_MARKER,
                _condense,
                _extract_digest_body,
                _flatten_content,
            )

            dropped = full_history[: len(full_history) - len(window)]
            transcript_parts: List[str] = []
            prev_body = prev_digest and _extract_digest_body(str(prev_digest))
            if prev_body:
                transcript_parts.append(f"〔更早轮次对话要点〕\n{prev_body}")
            role_label = {
                "user": "用户",
                "assistant": "助手",
                "system": "系统",
            }
            for msg in dropped or []:
                role = (msg.get("role") or "").strip()
                text = _flatten_content(msg.get("content"))
                tool_text = _flatten_content(trusted_tool_run_text(msg))
                if tool_text:
                    text = f"{text} · 工具结果：{tool_text}".strip(" ·") if text else tool_text
                if not text:
                    continue
                transcript_parts.append(
                    f"{role_label.get(role, role or '未知')}：{text}"
                )
            if not transcript_parts:
                return None
            transcript = "\n".join(transcript_parts)

            llm = None
            if any((agent_id, agent_name, version_id)):
                try:
                    from app.services.ai.config import AgentConfigProvider
                    from app.services.ai.context_manager import AgentContextManager

                    agent_config, _ = await AgentContextManager.resolve_agent_config(
                        window,
                        agent_id=agent_id,
                        agent_name=agent_name,
                        version_id=version_id,
                        enable_multi_agent=False,
                        force_data_query=False,
                    )
                    if agent_config is not None:
                        llm = await AgentConfigProvider.get_configured_llm(
                            streaming=False, config=agent_config
                        )
                except Exception as exc:
                    logger.warning(
                        "[Compaction][LLM digest] Failed to resolve current-agent model: %s",
                        exc,
                    )
                    llm = None
            if llm is None:
                try:
                    from app.services.ai.config import AgentConfigProvider

                    llm = await AgentConfigProvider.get_fallback_llm(streaming=False)
                except Exception as exc:
                    logger.warning(
                        "[Compaction][LLM digest] No fallback LLM available: %s", exc
                    )
                    return None
            if llm is None:
                logger.warning(
                    "[Compaction][LLM digest] No available LLM, fallback to deterministic"
                )
                return None

            from app.services.ai.conversation_summarizer import ConversationSummarizer
            from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
            from app.services.ai.runtime.agentscope.messages import (
                RuntimeContentBlock,
                RuntimeMessage,
            )

            system_prompt = (
                "你是上下文压缩助手。会话多轮上下文超过窗口后，请把下面"
                "「更早轮次无法直接保留的对话」压缩成一段简洁的中文要点，仅输出正文，"
                "不要输出 JSON、代码块或标题符号。要点需尽量覆盖：关键事实、已确认决策、"
                "未完成事项、与后续轮次相关的核心对象/术语。不要编造对话中未出现的信息。"
                f"全文控制在 {max_chars} 字以内。"
            )
            chat_client = chat_client_from_handle(llm)
            llm_messages = [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=system_prompt)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=transcript)],
                ),
            ]
            async with asyncio.timeout(15):
                raw = await ConversationSummarizer._generate_with_retry(
                    chat_client, llm_messages, max_retries=2
                )
            body = (raw or "").strip()
            if not body:
                return None
            body = _condense(body, max_chars)
            content = (
                f"{COMPACTION_MARKER}\n"
                "以下是更早轮次对话的要点（已由模型压缩，仅供理解上下文与指代，不要逐条复述）：\n"
                f"{body}"
            )
            return {"role": "system", "content": content}
        except Exception as exc:
            logger.warning(
                "[Compaction][LLM digest] Semantic summary failed, fallback to deterministic: %s",
                exc,
            )
            return None
