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
from app.services.secrets.guard import build_storage_scanner

logger = logging.getLogger(__name__)


def resolve_history_model_name(agent_config: Any) -> Optional[str]:
    """历史行记录的模型名：会话级覆盖（输入框选的）优先于智能体版本配置。

    与 ``app/services/ai/model_override.py`` 共用同一套优先级，避免「实际用了
    gpt-oss-120b、用量报表却记成版本配置模型」这种对不上账的情况。
    """
    from app.services.ai.model_override import (
        resolve_effective_model_name,
        session_model_override,
    )

    return resolve_effective_model_name(
        session_model=session_model_override(),
        config_model=getattr(agent_config, "model_name", None),
    ) or None

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

# 数据库判定「唯一键冲突」的典型报错片段：MySQL 1062 / PG 23505 / SQLite UNIQUE。
# 并发下两个事务同时没查到旧行并同时 INSERT 时命中，回滚后重走查改即可收敛。
_UNIQUE_CONFLICT_HINTS = (
    "duplicate entry",
    "unique constraint",
    "duplicate key value",
    "1062",
    "23505",
)


def _looks_like_length_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _LENGTH_ERROR_HINTS)


def _looks_like_unique_conflict(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _UNIQUE_CONFLICT_HINTS)


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
        *,
        trace_logs_to_save: Optional[List[AgentExecutionStep]] = None,
        accumulate_tokens: bool = False,
        accumulate_duration: bool = False,
        dedup_trace_logs: bool = False,
        offset_trace_steps: bool = False,
    ) -> bool:
        """
        High-level method to handle all audit logging (Trace Logs + History).
        Encapsulates model ID resolution.

        Returns ``True`` 表示本轮历史已落库；``False`` 表示未落库（无 agent_config
        或重试后仍失败）。调用方必须据此判断「历史是否可查」，不能默认成功。
        """
        logs_to_persist = (
            trace_buffer if trace_logs_to_save is None else trace_logs_to_save
        )
        logger.info(
            f"[Audit] Starting transaction log for {trace_id}. "
            f"Buffer size: {len(trace_buffer) if trace_buffer else 0}, "
            f"Persist trace size: {len(logs_to_persist) if logs_to_persist else 0}"
        )
        # 1. Save Trace Logs
        if logs_to_persist:
            if dedup_trace_logs or offset_trace_steps:
                await AuditManager.save_trace_logs(
                    trace_id,
                    logs_to_persist,
                    dedup_existing=dedup_trace_logs,
                    offset_step_number=offset_trace_steps,
                )
            else:
                await AuditManager.save_trace_logs(trace_id, logs_to_persist)
        else:
            logger.warning(f"[Audit] No trace logs to save for {trace_id}")

        # 2. Save History
        if agent_config:
            model_config_id = None
            # 历史行上的「本轮模型」必须是真实生效的那个：用户在输入框换过模型时，
            # 版本配置的 model_name 只是默认值。用量报表、会话导出都读这一列，
            # 读错就等于把 A 模型的消耗记到 B 模型头上。
            model_id_snapshot = resolve_history_model_name(agent_config)
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

            extra_history_kwargs: Dict[str, Any] = {}
            if accumulate_tokens:
                extra_history_kwargs["accumulate_tokens"] = True
            if accumulate_duration:
                extra_history_kwargs["accumulate_duration"] = True

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
                **extra_history_kwargs,
            )

        logger.warning(
            f"[Audit] Skipped history for {trace_id}: agent_config is empty, "
            "the turn will not be queryable from /chat/history"
        )
        return False

    @staticmethod
    async def save_trace_logs(
        trace_id: str,
        logs: List[AgentExecutionStep],
        *,
        dedup_existing: bool = False,
        offset_step_number: bool = False,
    ):
        """
        Persists a list of execution steps to the database.

        挂起轮先落前半段、恢复轮再补后半段时，调用方可传 ``dedup_existing=True``
        跳过已存在的 ``(step_number, event_type, span_id)``，跨进程恢复重建空
        ``trace_buffer`` 时另传 ``offset_step_number=True`` 顺延步骤号，避免轨迹
        步骤重复或从 1 倒挂。
        """
        if not logs:
            return

        # 一次快照、全程复用：循环里每条都重建扫描器会把排序/去重白做一遍。
        scanner = await build_storage_scanner()
        # 但要两层：`scanner` 只认「配置里的密钥 + MCP 固定 Token」
        # （registry._collect 不加载 sys_secrets），凭据中心的凭据它看不见。
        # 认得出那些值的只有 redact —— 它读的是本次调用注入时登记的明文。
        # outbound_audit 一直是两层，这里原来只有一层，远端把凭据回显在响应里
        # 就会原样落进 trace。顺序与那边保持一致：先 redact，再 scanner。
        from app.services.secrets.redact import redact_text, redact_value

        try:
            async with AsyncSessionLocal() as session:
                existing_keys: set[tuple[Any, Any, Any]] = set()
                existing_span_ids: set[str] = set()
                max_existing_step = 0
                if dedup_existing or offset_step_number:
                    from sqlalchemy import select

                    existing_rows = (
                        await session.execute(
                            select(
                                AgentExecutionTrace.step_number,
                                AgentExecutionTrace.event_type,
                                AgentExecutionTrace.span_id,
                            ).where(AgentExecutionTrace.trace_id == trace_id)
                        )
                    ).all()
                    for row in existing_rows:
                        existing_keys.add((row[0], row[1], row[2]))
                        if row[2]:
                            existing_span_ids.add(str(row[2]))
                        step_val = int(row[0] or 0)
                        if step_val > max_existing_step:
                            max_existing_step = step_val

                orm_objects = []
                next_offset_step = max_existing_step
                for log in logs:
                    raw_step = int(getattr(log, "step_number", 0) or 0)
                    span_id = getattr(log, "span_id", None)
                    if dedup_existing:
                        if offset_step_number:
                            # 跨进程恢复重建 runner 后 step_number 从 0/1 重新起算，
                            # 不能拿未偏移的 (raw_step, event_type, None) 跟挂起轮旧步骤判重，
                            # 否则大量 span_id=None 的新步骤会被误判重复而静默丢弃；
                            # 跨进程去重仅针对全局唯一的非空 span_id。
                            if span_id and str(span_id) in existing_span_ids:
                                continue
                        elif (raw_step, log.event_type, span_id) in existing_keys:
                            continue

                    if offset_step_number and max_existing_step > 0:
                        candidate = (
                            max_existing_step + raw_step
                            if raw_step > 0
                            else next_offset_step + 1
                        )
                        step_number = max(candidate, next_offset_step + 1)
                        next_offset_step = step_number
                    else:
                        step_number = log.step_number

                    if dedup_existing:
                        if span_id:
                            existing_span_ids.add(str(span_id))
                        if not offset_step_number:
                            existing_keys.add((step_number, log.event_type, span_id))

                    orm_objects.append(AgentExecutionTrace(
                        trace_id=trace_id,
                        step_number=step_number,
                        event_type=log.event_type,
                        agent_name=log.agent_name,
                        tool_name=log.tool_name,
                        # 先擦凭据、再截断：顺序反了会把密钥切成两半，
                        # 半截值既匹配不上指纹、又仍然泄露关键片段。
                        tool_input=bound_audit_payload(
                            scanner.mask_data(redact_value(log.tool_input, log_hits=False))
                        ),
                        tool_output=bound_audit_payload(
                            scanner.mask_data(redact_value(log.tool_output, log_hits=False))
                        ),
                        execution_time_ms=log.execution_time_ms,
                        status=log.status,
                        # 错误信息是最容易夹带连接串/密钥的地方（"Access denied for ..."），
                        # 之前只靠键名规则过滤，用户名密码写进 message 就会漏。
                        error_message=scanner.mask_text(
                            redact_text(log.error_message, log_hits=False)
                        )
                        if isinstance(log.error_message, str)
                        else log.error_message,
                        model=log.model,
                        temperature=log.temperature,
                        prompt_tokens=getattr(log, 'prompt_tokens', 0) or 0,
                        completion_tokens=getattr(log, 'completion_tokens', 0) or 0,
                        total_tokens=getattr(log, 'total_tokens', 0) or 0,
                        span_id=span_id,
                        parent_span_id=getattr(log, 'parent_span_id', None),
                        meta_info=scanner.mask_data(
                            redact_value(getattr(log, 'meta_info', None), log_hits=False)
                        ),
                        created_at=log.timestamp
                    ))
                if not orm_objects:
                    return
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
        *,
        accumulate_tokens: bool = False,
        accumulate_duration: bool = False,
    ) -> bool:
        """
        Saves or updates (idempotent upsert by ``trace_id``) the high-level conversation entry.

        挂起轮（``awaiting_permission`` / ``awaiting_external_execution``）先落库，
        恢复轮用同一 ``trace_id`` 再次调用时就地更新该行；若并发下同时插入触发唯一键冲突，
        自动重试并转为更新。MySQL 与 PostgreSQL 共用同一套 ORM 先查后改语义，避免方言分裂。

        三处长文本（query / summary / reasoning_content）在落库前按 UTF-8 字节做
        保底截断；若数据库仍判定超长（MySQL 1406 / PG 22001），降级到更小阈值再试
        一次。历史行装不下时宁可少存尾部，也不能让整轮历史从 /chat/history 消失。

        Returns:
            True: 已落库（极端情况下内容带截断标记）
            False: 重试后仍未落库，调用方必须按「历史不可查」处理
        """
        from sqlalchemy import select
        from app.models.audit import AgentExecutionHistory

        try:
            history_user_id = require_user_id(user_info)
        except Exception as exc:
            logger.error(
                f"[Audit] history 缺少可靠用户身份，本轮历史未落库 trace={trace_id}: {exc}"
            )
            return False

        username = user_info.get("user_name") if user_info else None

        # 脱敏必须发生在截断之前：先截断会把密钥切成半截，半截值既匹配不上
        # 已知凭据、又照样把关键片段留在库里。
        scanner = await build_storage_scanner()
        # 与 save_trace_logs 同一个口径：scanner 不加载凭据中心，只有 redact 认得出
        # 本次注入的明文。这里比 trace 更容易漏——摘要和推理过程是模型写的散文，
        # 没有任何「敏感键名」可供 scanner 判断。
        from app.services.secrets.redact import redact_text, redact_value

        safe_query = scanner.mask_text(redact_text(query, log_hits=False)) if isinstance(query, str) else query
        safe_summary = (
            scanner.mask_text(redact_text(summary, log_hits=False))
            if isinstance(summary, str)
            else summary
        )
        safe_reasoning = (
            scanner.mask_text(redact_text(reasoning_content, log_hits=False))
            if isinstance(reasoning_content, str)
            else reasoning_content
        )
        safe_timeline = scanner.mask_data(redact_value(process_timeline, log_hits=False))

        bounded_query, query_bytes = truncate_text_bytes(
            safe_query, max_bytes=MAX_HISTORY_TEXT_BYTES
        )
        bounded_summary, summary_bytes = truncate_text_bytes(
            safe_summary, max_bytes=MAX_HISTORY_TEXT_BYTES
        )
        bounded_reasoning, reasoning_bytes = truncate_text_bytes(
            safe_reasoning, max_bytes=MAX_HISTORY_TEXT_BYTES
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
                existing = (
                    await session.execute(
                        select(AgentExecutionHistory).where(
                            AgentExecutionHistory.trace_id == trace_id
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    if agent_id:
                        existing.agent_id = agent_id
                    if conversation_id:
                        existing.conversation_id = conversation_id
                    if history_user_id and not existing.user_id:
                        existing.user_id = history_user_id
                    if username and not existing.username:
                        existing.username = username
                    # 恢复轮若异常缺少 user_query 或因强制取消跳过前序回捞，
                    # 不能把挂起轮已落库的原始提问、前半段正文、思考或时间线抹空。
                    if query_value:
                        existing.query = query_value
                    if summary_value:
                        existing.summary = summary_value
                    if reasoning_value:
                        existing.reasoning_content = reasoning_value
                    if safe_timeline:
                        existing.process_timeline = safe_timeline
                    existing.status = status
                    if accumulate_duration:
                        existing.execution_time_ms = float(
                            existing.execution_time_ms or 0.0
                        ) + float(execution_time_ms or 0.0)
                    else:
                        existing.execution_time_ms = execution_time_ms
                    if agent_version is not None:
                        existing.agent_version = agent_version
                    if model_id is not None:
                        existing.model_id = model_id
                    if model_config_id is not None:
                        existing.model_config_id = model_config_id
                    if accumulate_tokens:
                        existing.prompt_tokens = int(
                            existing.prompt_tokens or 0
                        ) + int(prompt_tokens or 0)
                        existing.completion_tokens = int(
                            existing.completion_tokens or 0
                        ) + int(completion_tokens or 0)
                        existing.total_tokens = int(
                            existing.total_tokens or 0
                        ) + int(total_tokens or 0)
                    else:
                        existing.prompt_tokens = prompt_tokens
                        existing.completion_tokens = completion_tokens
                        existing.total_tokens = total_tokens
                    if has_data_output is not None:
                        existing.has_data_output = 1 if has_data_output else 0
                    if agent_name is not None:
                        existing.agent_name = agent_name
                    if agent_display_name is not None:
                        existing.agent_display_name = agent_display_name
                else:
                    history_entry = AgentExecutionHistory(
                        trace_id=trace_id,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        user_id=history_user_id,
                        username=username,
                        query=query_value,
                        summary=summary_value,
                        reasoning_content=reasoning_value,
                        process_timeline=safe_timeline,
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
            if _looks_like_unique_conflict(first_error):
                logger.warning(
                    f"[Audit] history 首次写入命中唯一键冲突，转为查改重试 {size_log} "
                    f"error={first_error}"
                )
                retry_max_bytes = MAX_HISTORY_TEXT_BYTES
            else:
                retry_max_bytes = (
                    FALLBACK_HISTORY_TEXT_BYTES
                    if _looks_like_length_error(first_error)
                    else MAX_HISTORY_TEXT_BYTES
                )
                logger.error(
                    f"[Audit] history 首次保存失败 {size_log} "
                    f"retry_budget={retry_max_bytes}B error={first_error}"
                )

        retry_query, _ = truncate_text_bytes(safe_query, max_bytes=retry_max_bytes)
        retry_summary, _ = truncate_text_bytes(safe_summary, max_bytes=retry_max_bytes)
        retry_reasoning, _ = truncate_text_bytes(
            safe_reasoning, max_bytes=retry_max_bytes
        )

        try:
            await _write(retry_query, retry_summary, retry_reasoning)
            logger.warning(
                f"[Audit] history 重试成功 {size_log} "
                f"retry_budget={retry_max_bytes}B（内容可能已截断或已转更新）"
            )
            return True
        except Exception as retry_error:
            logger.error(
                f"[Audit] history 重试仍失败，本轮历史不可查 {size_log} "
                f"retry_budget={retry_max_bytes}B error={retry_error}"
            )
            return False

