import logging
from typing import List, Any, Optional, Dict
from app.schemas.agent import AgentExecutionStep
from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionTrace
from app.services.ai.conversation_identity import require_user_id
from app.services.ai.audit_payload import (
    FALLBACK_HISTORY_TEXT_BYTES,
    MAX_HISTORY_TEXT_BYTES,
    bound_audit_payload,
    truncate_text_bytes,
)

logger = logging.getLogger(__name__)

# 仅这些步骤类型对应真实的 LLM API 调用；tool_call / router 等不计入 Token。
# model_call：AgentScope ReAct 每次 MODEL_CALL_END 一条，与前端 SSE 累加口径一致。
# thought / synthesis：直连 LLM 或总结阶段（无 model_call 时由 synthesis 承载单次调用）。
_LLM_TOKEN_STEP_EVENTS = frozenset({"thought", "synthesis", "model_call"})

# 数据库判定「值超长」的典型报错片段：MySQL 1406 / PG 22001。
# 命中后必须降级截断重试——这类错误重试原样内容只会再次失败。
_LENGTH_ERROR_HINTS = (
    "data too long",
    "value too long",
    "1406",
    "22001",
)


def _looks_like_length_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _LENGTH_ERROR_HINTS)


def aggregate_tokens_from_trace_buffer(trace_buffer: List[AgentExecutionStep]) -> tuple[int, int, int]:
    """
    从 trace 步骤汇总会话级 Token。
    - 每条 LLM API 调用只应出现一次（model_call 或带 usage 的 synthesis/thought）
    - ReAct 路径：多次 model_call 累加；末尾 synthesis 若为 0 token 则自动忽略
    - 无 AgentScope 的单次直答：仅 synthesis/thought 带 usage
    - 仅有 total_tokens、无分项的步骤计入 orphan 总量
    """
    if not trace_buffer:
        return 0, 0, 0

    prompt_sum = 0
    completion_sum = 0
    orphan_total = 0

    for step in trace_buffer:
        if getattr(step, "event_type", None) not in _LLM_TOKEN_STEP_EVENTS:
            continue
        p = int(getattr(step, "prompt_tokens", 0) or 0)
        c = int(getattr(step, "completion_tokens", 0) or 0)
        t = int(getattr(step, "total_tokens", 0) or 0)
        if p <= 0 and c <= 0 and t <= 0:
            continue
        if p > 0 or c > 0:
            prompt_sum += p
            completion_sum += c
        elif t > 0:
            orphan_total += t

    if prompt_sum > 0 or completion_sum > 0:
        return prompt_sum, completion_sum, prompt_sum + completion_sum + orphan_total
    return 0, 0, orphan_total


class AuditManager:
    """
    Handles trace logging and auditing for agent executions.
    """
    
    @staticmethod
    async def log_transaction(
        trace_id: str,
        agent_config: Any,
        user_query: str,
        response_content: str,
        user_info: Optional[Dict[str, Any]],
        status: str,
        duration: float,
        trace_buffer: List[AgentExecutionStep],
        conversation_id: Optional[str] = None,
        reasoning_content: Optional[str] = None,
        process_timeline: Optional[List[Dict[str, Any]]] = None,
        has_data_output: Optional[bool] = None,
    ) -> bool:
        """
        High-level method to handle all audit logging (Trace Logs + History).
        Encapsulates model ID resolution.

        Returns ``True`` 表示本轮历史已落库；``False`` 表示未落库（无 agent_config
        或重试后仍失败）。调用方必须据此判断「历史是否可查」，不能默认成功。
        """
        logger.info(f"[Audit] Starting transaction log for {trace_id}. Buffer size: {len(trace_buffer) if trace_buffer else 0}")
        # 1. Save Trace Logs
        if trace_buffer:
            await AuditManager.save_trace_logs(trace_id, trace_buffer)
        else:
            logger.warning(f"[Audit] No trace logs to save for {trace_id}")

        # 2. Save History
        if agent_config:
            model_config_id = None
            model_id_snapshot = agent_config.model_name
            agent_version = agent_config.agent_version
            agent_id = agent_config.agent_id

            try:
                from app.models.ai_model import AIModel
                from sqlalchemy import select, or_
                
                async with AsyncSessionLocal() as db_session:
                    stmt = select(AIModel).where(
                    AIModel.is_active == True,
                    or_(AIModel.model_id == model_id_snapshot, AIModel.name == model_id_snapshot)
                    )
                    res = await db_session.execute(stmt)
                    aim = res.scalars().first()
                    if aim:
                        model_config_id = aim.id
                        model_id_snapshot = aim.model_id
            except Exception as ex:
                logger.warning(f"Failed to resolve model ID for auditing: {ex}")

            prompt_tokens_sum, completion_tokens_sum, total_tokens_sum = (
                aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
            )

            return await AuditManager.save_history(
                trace_id=trace_id,
                agent_id=agent_id,
                query=user_query,
                summary=response_content,
                user_info=user_info,
                status=status,
                execution_time_ms=duration,
                agent_version=agent_version,
                model_id=model_id_snapshot,
                model_config_id=model_config_id,
                conversation_id=conversation_id,
                prompt_tokens=prompt_tokens_sum,
                completion_tokens=completion_tokens_sum,
                total_tokens=total_tokens_sum,
                reasoning_content=reasoning_content,
                process_timeline=process_timeline,
                has_data_output=has_data_output,
                agent_name=getattr(agent_config, "agent_name", None) or None,
                agent_display_name=getattr(agent_config, "agent_display_name", None) or None,
            )

        logger.warning(
            f"[Audit] Skipped history for {trace_id}: agent_config is empty, "
            "the turn will not be queryable from /chat/history"
        )
        return False

    @staticmethod
    async def save_trace_logs(trace_id: str, logs: List[AgentExecutionStep]):
        """
        Persists a list of execution steps to the database.
        """
        if not logs:
            return
        
        try:
            async with AsyncSessionLocal() as session:
                orm_objects = []
                for log in logs:
                    orm_objects.append(AgentExecutionTrace(
                        trace_id=trace_id,
                        step_number=log.step_number,
                        event_type=log.event_type,
                        agent_name=log.agent_name,
                        tool_name=log.tool_name,
                        # Bound only the audit copy. AgentScope has already
                        # consumed the original result and keeps using it.
                        tool_input=bound_audit_payload(log.tool_input),
                        tool_output=bound_audit_payload(log.tool_output),
                        execution_time_ms=log.execution_time_ms,
                        status=log.status,
                        error_message=log.error_message,
                        model=log.model,
                        temperature=log.temperature,
                        prompt_tokens=getattr(log, 'prompt_tokens', 0) or 0,
                        completion_tokens=getattr(log, 'completion_tokens', 0) or 0,
                        total_tokens=getattr(log, 'total_tokens', 0) or 0,
                        span_id=getattr(log, 'span_id', None),
                        parent_span_id=getattr(log, 'parent_span_id', None),
                        meta_info=getattr(log, 'meta_info', None),
                        created_at=log.timestamp
                    ))
                session.add_all(orm_objects)
                await session.commit()
                logger.info(f"Successfully saved {len(orm_objects)} trace logs for {trace_id}")
        except Exception as e:
            logger.error(f"Failed to save trace logs for {trace_id}: {e}")

    @staticmethod
    async def save_history(
        trace_id: str, 
        agent_id: str, 
        query: str, 
        summary: str,
        user_info: dict,
        status: str = "success",
        execution_time_ms: float = 0.0,
        agent_version: str = None,
        model_id: str = None,
        model_config_id: str = None,
        conversation_id: str = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        reasoning_content: Optional[str] = None,
        process_timeline: Optional[List[Dict[str, Any]]] = None,
        has_data_output: Optional[bool] = None,
        agent_name: Optional[str] = None,
        agent_display_name: Optional[str] = None,
    ) -> bool:
        """
        Saves the high-level conversation entry.

        三处长文本（query / summary / reasoning_content）在落库前按 UTF-8 字节做
        保底截断；若数据库仍判定超长（MySQL 1406 / PG 22001），降级到更小阈值再试
        一次。历史行装不下时宁可少存尾部，也不能让整轮历史从 /chat/history 消失。

        Returns:
            True: 已落库（极端情况下内容带截断标记）
            False: 重试后仍未落库，调用方必须按「历史不可查」处理
        """
        from app.models.audit import AgentExecutionHistory

        try:
            history_user_id = require_user_id(user_info)
        except Exception as exc:
            logger.error(
                f"[Audit] history 缺少可靠用户身份，本轮历史未落库 trace={trace_id}: {exc}"
            )
            return False

        username = user_info.get("user_name") if user_info else None

        bounded_query, query_bytes = truncate_text_bytes(
            query, max_bytes=MAX_HISTORY_TEXT_BYTES
        )
        bounded_summary, summary_bytes = truncate_text_bytes(
            summary, max_bytes=MAX_HISTORY_TEXT_BYTES
        )
        bounded_reasoning, reasoning_bytes = truncate_text_bytes(
            reasoning_content, max_bytes=MAX_HISTORY_TEXT_BYTES
        )

        size_log = (
            f"trace={trace_id} query={query_bytes}B summary={summary_bytes}B "
            f"reasoning={reasoning_bytes}B"
        )
        if (
            query_bytes > MAX_HISTORY_TEXT_BYTES
            or summary_bytes > MAX_HISTORY_TEXT_BYTES
            or reasoning_bytes > MAX_HISTORY_TEXT_BYTES
        ):
            logger.warning(
                f"[Audit] history 长文本超出保底阈值 {MAX_HISTORY_TEXT_BYTES}B，"
                f"已截断后落库 {size_log}"
            )

        async def _write(
            query_value: Optional[str],
            summary_value: Optional[str],
            reasoning_value: Optional[str],
        ) -> None:
            async with AsyncSessionLocal() as session:
                history_entry = AgentExecutionHistory(
                    trace_id=trace_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    user_id=history_user_id,
                    username=username,
                    query=query_value,
                    summary=summary_value,
                    reasoning_content=reasoning_value,
                    process_timeline=process_timeline,
                    status=status,
                    execution_time_ms=execution_time_ms,
                    agent_version=agent_version,
                    model_id=model_id,
                    model_config_id=model_config_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    has_data_output=(1 if has_data_output else 0),
                    # 身份快照：写入时刻固化，避免智能体删除/权限变化后历史失去署名
                    agent_name=agent_name,
                    agent_display_name=agent_display_name,
                )
                session.add(history_entry)
                await session.commit()

        try:
            await _write(bounded_query, bounded_summary, bounded_reasoning)
            logger.info(
                f"Saved conversation history for trace {trace_id} (Version: {agent_version})"
            )
            return True
        except Exception as first_error:
            retry_max_bytes = (
                FALLBACK_HISTORY_TEXT_BYTES
                if _looks_like_length_error(first_error)
                else MAX_HISTORY_TEXT_BYTES
            )
            logger.error(
                f"[Audit] history 首次保存失败 {size_log} "
                f"retry_budget={retry_max_bytes}B error={first_error}"
            )

        retry_query, _ = truncate_text_bytes(query, max_bytes=retry_max_bytes)
        retry_summary, _ = truncate_text_bytes(summary, max_bytes=retry_max_bytes)
        retry_reasoning, _ = truncate_text_bytes(
            reasoning_content, max_bytes=retry_max_bytes
        )

        try:
            await _write(retry_query, retry_summary, retry_reasoning)
            logger.warning(
                f"[Audit] history 重试成功 {size_log} "
                f"retry_budget={retry_max_bytes}B（内容可能已截断）"
            )
            return True
        except Exception as retry_error:
            logger.error(
                f"[Audit] history 重试仍失败，本轮历史不可查 {size_log} "
                f"retry_budget={retry_max_bytes}B error={retry_error}"
            )
            return False

