import logging
from typing import List, Any, Optional, Dict
from app.schemas.agent import AgentExecutionStep
from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionTrace

logger = logging.getLogger(__name__)

# 仅这些步骤类型对应真实的 LLM API 调用；tool_call / router 等不计入 Token。
# model_call：AgentScope ReAct 每次 MODEL_CALL_END 一条，与前端 SSE 累加口径一致。
# thought / synthesis：直连 LLM 或总结阶段（无 model_call 时由 synthesis 承载单次调用）。
_LLM_TOKEN_STEP_EVENTS = frozenset({"thought", "synthesis", "model_call"})


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
        conversation_id: Optional[str] = None
    ):
        """
        High-level method to handle all audit logging (Trace Logs + History).
        Encapsulates model ID resolution.
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

            await AuditManager.save_history(
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
                total_tokens=total_tokens_sum
            )

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
                        tool_input=log.tool_input,
                        tool_output=log.tool_output,
                        execution_time_ms=log.execution_time_ms,
                        status=log.status,
                        error_message=log.error_message,
                        model=log.model,
                        temperature=log.temperature,
                        prompt_tokens=getattr(log, 'prompt_tokens', 0) or 0,
                        completion_tokens=getattr(log, 'completion_tokens', 0) or 0,
                        total_tokens=getattr(log, 'total_tokens', 0) or 0,
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
        total_tokens: int = 0
    ):
        """
        Saves the high-level conversation entry.
        """
        try:
            from app.models.audit import AgentExecutionHistory
            
            async with AsyncSessionLocal() as session:
                history_entry = AgentExecutionHistory(
                    trace_id=trace_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    user_id=user_info.get("user_id") if user_info else None,
                    username=user_info.get("user_name") if user_info else None,
                    query=query,
                    summary=summary,
                    status=status,
                    execution_time_ms=execution_time_ms,
                    agent_version=agent_version,
                    model_id=model_id,
                    model_config_id=model_config_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens
                )
                session.add(history_entry)
                await session.commit()
                logger.info(f"Saved conversation history for trace {trace_id} (Version: {agent_version})")
        except Exception as e:
            logger.error(f"Failed to save history for {trace_id}: {e}")
