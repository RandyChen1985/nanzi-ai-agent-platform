import logging
import uuid
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.orm import AsyncSessionLocal, engine
from app.core import redis
from app.models.task import AgentScheduledTask
from app.models.user import User
from app.models.saved_report import (
    PortalSavedReport,
    PortalSavedReportDigestDelivery,
    PortalSavedReportRun,
    PortalSavedReportSubscription,
)
from app.services.platform_timezone import get_cached_platform_timezone

logger = logging.getLogger(__name__)

BUSY_CONVERSATION_MESSAGE = "当前会话正在处理中"
NO_TOOL_EXECUTION_MESSAGE = "自动任务未实际调用任何工具"
TASK_RUN_CONVERSATION_SUFFIX_LEN = len("_run_") + 12
MAX_CONVERSATION_ID_LEN = 50
INCOMPLETE_TASK_STATUSES = frozenset({"awaiting_permission", "awaiting_external_execution"})
FAILED_TASK_STATUSES = frozenset({"error"})
TASK_METRICS_KEY = "task_metrics"
TASK_METRIC_DEFAULTS = {
    "trigger_count": 0,
    "success_count": 0,
    "failure_count": 0,
    "skipped_count": 0,
    "consecutive_failures": 0,
    "health_status": "unknown",
    "last_status": None,
    "last_message": None,
    "last_error": None,
    "last_trace_id": None,
    "last_started_at": None,
    "last_finished_at": None,
    "last_alert_at": None,
    # 结果投递与任务执行状态解耦：投递失败不改变任务执行成败
    "last_delivery_status": None,
    "last_delivery_error": None,
    "last_delivery_at": None,
    "delivery_failure_streak": 0,
}

# 执行超时：调度层对单次运行包一层总超时，防止卡住的 LLM/工具调用无限占用
TASK_EXECUTION_TIMEOUT_KEY = "execution_timeout_seconds"
DEFAULT_TASK_EXECUTION_TIMEOUT_SEC = 1800
MIN_TASK_EXECUTION_TIMEOUT_SEC = 60
MAX_TASK_EXECUTION_TIMEOUT_SEC = 7200

# 失败重试：仅定时触发的运行参与重试，手动触发不重试
TASK_MAX_RETRIES_KEY = "max_retries"
TASK_RETRY_DELAY_KEY = "retry_delay_seconds"
MAX_TASK_RETRIES = 3
DEFAULT_TASK_RETRY_DELAY_SEC = 300
MIN_TASK_RETRY_DELAY_SEC = 60
MAX_TASK_RETRY_DELAY_SEC = 3600
SCHEDULER_RECONCILE_INTERVAL_SEC = 30

# 执行期互斥锁：执行期间持有、结束释放（不再是「同一分钟去重」），手动触发同样受锁保护
_TASK_EXEC_LOCK_PREFIX = "lock:task_exec:"
_TASK_EXEC_LOCK_TTL_BUFFER_SEC = 300
_RELEASE_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def _clamp_int(raw: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def execution_timeout_from_task_config(config: Optional[Dict[str, Any]]) -> int:
    cfg = config if isinstance(config, dict) else {}
    return _clamp_int(
        cfg.get(TASK_EXECUTION_TIMEOUT_KEY),
        default=DEFAULT_TASK_EXECUTION_TIMEOUT_SEC,
        minimum=MIN_TASK_EXECUTION_TIMEOUT_SEC,
        maximum=MAX_TASK_EXECUTION_TIMEOUT_SEC,
    )


def retry_policy_from_task_config(config: Optional[Dict[str, Any]]) -> tuple:
    """返回 (max_retries, retry_delay_seconds)。默认不重试。"""
    cfg = config if isinstance(config, dict) else {}
    max_retries = _clamp_int(cfg.get(TASK_MAX_RETRIES_KEY), default=0, minimum=0, maximum=MAX_TASK_RETRIES)
    delay = _clamp_int(
        cfg.get(TASK_RETRY_DELAY_KEY),
        default=DEFAULT_TASK_RETRY_DELAY_SEC,
        minimum=MIN_TASK_RETRY_DELAY_SEC,
        maximum=MAX_TASK_RETRY_DELAY_SEC,
    )
    return max_retries, delay


def _task_execution_lock_key(task_id: int) -> str:
    return f"{_TASK_EXEC_LOCK_PREFIX}{task_id}"


def _task_id_from_job_id(job_id: str) -> Optional[int]:
    if not job_id.startswith("task_"):
        return None
    raw_id = job_id[len("task_"):].split("_retry_", 1)[0]
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def _saved_report_subscription_id_from_job_id(job_id: str) -> Optional[int]:
    prefix = "saved_report_subscription_"
    if not job_id.startswith(prefix):
        return None
    try:
        return int(job_id[len(prefix):])
    except (TypeError, ValueError):
        return None


async def _acquire_task_execution_lock(task_id: int, ttl_sec: int) -> Optional[str]:
    """获取任务执行期互斥锁。返回锁 token；Redis 不可用时降级为不加锁（返回空 token）。"""
    if redis.redis_client is None:
        logger.warning("Redis unavailable, task %s runs without execution lock.", task_id)
        return ""
    token = uuid.uuid4().hex
    try:
        acquired = await redis.redis_client.set(
            _task_execution_lock_key(task_id), token, ex=ttl_sec, nx=True
        )
    except Exception as lock_err:
        logger.warning("Failed to acquire execution lock for task %s: %s", task_id, lock_err)
        return ""
    return token if acquired else None


async def _release_task_execution_lock(task_id: int, token: Optional[str]) -> None:
    if not token or redis.redis_client is None:
        return
    try:
        await redis.redis_client.eval(_RELEASE_LOCK_SCRIPT, 1, _task_execution_lock_key(task_id), token)
    except Exception as unlock_err:
        logger.warning("Failed to release execution lock for task %s: %s", task_id, unlock_err)


def _task_permission_options(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.services.task_execution_options import permission_options_from_task_config

    return permission_options_from_task_config(config)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _task_config(task: AgentScheduledTask) -> Dict[str, Any]:
    return dict(task.config or {})


def _normalize_task_metrics(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get(TASK_METRICS_KEY)
    metrics = dict(raw) if isinstance(raw, dict) else {}
    normalized = {**TASK_METRIC_DEFAULTS, **metrics}
    for key in ("trigger_count", "success_count", "failure_count", "skipped_count", "consecutive_failures", "delivery_failure_streak"):
        try:
            normalized[key] = int(normalized.get(key) or 0)
        except (TypeError, ValueError):
            normalized[key] = 0
    return normalized


def _task_metrics(task: AgentScheduledTask) -> Dict[str, Any]:
    return _normalize_task_metrics(_task_config(task))


async def _write_task_metrics(
    session: AsyncSession,
    task: AgentScheduledTask,
    metrics: Dict[str, Any],
) -> None:
    config = _task_config(task)
    config[TASK_METRICS_KEY] = metrics
    await session.execute(
        update(AgentScheduledTask)
        .where(AgentScheduledTask.id == task.id)
        .values(config=config)
    )
    await session.commit()
    task.config = config


async def _mark_task_attempt_started(session: AsyncSession, task: AgentScheduledTask) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["trigger_count"] += 1
    metrics["last_status"] = "running"
    metrics["last_message"] = "任务已触发，正在执行"
    metrics["last_error"] = None
    metrics["last_started_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _mark_task_success(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    message: str,
) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["success_count"] += 1
    metrics["consecutive_failures"] = 0
    metrics["health_status"] = "healthy"
    metrics["last_status"] = "success"
    metrics["last_message"] = message
    metrics["last_error"] = None
    metrics["last_trace_id"] = trace_id
    metrics["last_finished_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _mark_task_failure(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    error: str,
) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["failure_count"] += 1
    metrics["consecutive_failures"] += 1
    metrics["health_status"] = "error" if metrics["consecutive_failures"] >= 3 else "warning"
    metrics["last_status"] = "failed"
    metrics["last_message"] = "任务执行失败"
    metrics["last_error"] = error
    metrics["last_trace_id"] = trace_id
    metrics["last_finished_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _mark_task_skipped(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    reason: str,
) -> Dict[str, Any]:
    metrics = _task_metrics(task)
    metrics["skipped_count"] += 1
    metrics["health_status"] = "skipped"
    metrics["last_status"] = "skipped"
    metrics["last_message"] = reason
    metrics["last_finished_at"] = _now_iso()
    await _write_task_metrics(session, task, metrics)
    return metrics


async def _record_task_delivery_result(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    ok: bool,
    notes: List[str],
) -> Dict[str, Any]:
    """记录结果通知投递状态；与任务执行成败解耦，只写 delivery 维度字段。"""
    metrics = _task_metrics(task)
    metrics["last_delivery_status"] = "success" if ok else "failed"
    metrics["last_delivery_error"] = None if ok else ("; ".join(notes)[:1000] or "投递失败")
    metrics["last_delivery_at"] = _now_iso()
    if ok:
        metrics["delivery_failure_streak"] = 0
    else:
        metrics["delivery_failure_streak"] = int(metrics.get("delivery_failure_streak") or 0) + 1
        metrics["last_message"] = "任务执行成功，但结果通知投递失败"
    await _write_task_metrics(session, task, metrics)
    return metrics


def _should_alert_delivery_failure(metrics: Dict[str, Any]) -> bool:
    streak = int(metrics.get("delivery_failure_streak") or 0)
    return streak == 1 or (streak > 0 and streak % 3 == 0)


def _task_run_conversation_prefix(task_conversation_id: str) -> str:
    base = (task_conversation_id or f"task_conv_{uuid.uuid4().hex[:12]}").strip()
    max_base_len = MAX_CONVERSATION_ID_LEN - TASK_RUN_CONVERSATION_SUFFIX_LEN
    return base[:max_base_len]


def _new_task_run_conversation_id(task_conversation_id: str) -> str:
    return f"{_task_run_conversation_prefix(task_conversation_id)}_run_{uuid.uuid4().hex[:12]}"


def _build_scheduled_task_prompt(
    task_id: int,
    agent_display_name: str,
    prompt: str,
    notification_channels: Optional[List[str]] = None,
) -> str:
    from app.services.task_notification_channels import build_notification_delivery_supplement

    global_rules = (
        "【🌐 TaskCenter 自动化全局执行规则】\n"
        "1. 无人值守模式：本次为后台自动触发运行，严禁向用户询问确认、索要参数或输出“准备开始/执行思路”等无意义聊天话术。\n"
        "2. 工具驱动执行：若任务涉及数据查询、系统交互或业务计算，必须立即发起真正的工具调用 (Tool Call) 收集数据。\n"
        "3. 结果生成要求：只有在所有必要的工具调用执行成功并获取数据后，才能整理最终的分析结论。\n"
        "4. 最终可见正文只交付结果：只输出面向终端用户的业务结论（含必要的标题、表格、建议）；"
        "禁止在正文中输出中间思考、执行规划、自我独白或工具过程复盘"
        "（如 “I'll / Let me / I need to / 我先 / 让我 / 接下来我要…”、缺少车站信息时如何兜底等叙述）。"
        "思考若走模型独立思考通道即可，绝不可写入最终回复正文；本轮推送将直接采用该正文。\n"
        "5. 禁增追问推荐：本次为后台离线交付，末尾严禁输出“您可能还想了解”、交互式推荐问题列表或快捷按钮（如 quick:xxx）。"
    )

    user_task = (
        f"【📋 任务执行指令 - ID: {task_id}】@{agent_display_name}\n"
        f"{str(prompt or '').strip()}"
    )

    parts = [global_rules, user_task]

    supplement = build_notification_delivery_supplement(notification_channels or [])
    if supplement:
        parts.append(supplement)

    return "\n\n".join(parts)


def _is_busy_task_result(result: Dict[str, Any]) -> bool:
    status = str((result or {}).get("status") or "").lower()
    content = str((result or {}).get("content") or "")
    return status == "error" and BUSY_CONVERSATION_MESSAGE in content


def _is_no_tool_execution_result(result: Dict[str, Any]) -> bool:
    status = str((result or {}).get("status") or "").lower()
    content = str((result or {}).get("content") or "")
    return status == "error" and NO_TOOL_EXECUTION_MESSAGE in content


def _is_incomplete_task_result(result: Dict[str, Any]) -> bool:
    """本次运行是否未成功交付。

    只看整轮最终状态：中途某一步工具失败但最终产出了结果的运行仍算成功，
    与执行历史里记录的整轮状态保持同一口径。
    """
    status = str((result or {}).get("status") or "").lower()
    return (
        status in INCOMPLETE_TASK_STATUSES
        or status in FAILED_TASK_STATUSES
        or _is_busy_task_result(result)
        or _is_no_tool_execution_result(result)
    )


def _task_result_error(result: Dict[str, Any]) -> str:
    status = str((result or {}).get("status") or "error")
    content = str((result or {}).get("content") or "").strip()
    if _is_busy_task_result(result):
        return "当前会话正在处理中，本次触发已跳过"
    if _is_no_tool_execution_result(result):
        return "自动任务未实际调用任何工具"
    if status in INCOMPLETE_TASK_STATUSES:
        return f"任务未完成，当前状态：{status}"
    return content[:500] or f"任务执行失败，状态：{status}"


def _should_alert_failure(metrics: Dict[str, Any]) -> bool:
    consecutive = int(metrics.get("consecutive_failures") or 0)
    return consecutive == 1 or consecutive % 3 == 0


async def _send_task_failure_alert(
    user_id: int,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    error: str,
    metrics: Dict[str, Any],
    kind: str = "execution",
) -> None:
    """任务失败/投递失败告警。

    站内信始终投递（保证用户能看到）；外部渠道优先走任务勾选的通知渠道，
    未勾选任何外部渠道时回退钉钉（兼容旧行为）。
    """
    try:
        from app.services.notification_service import NotificationService
        from app.services.portal_notification_service import PortalNotificationService
        from app.services.task_notification_channels import channels_from_task_config

        if kind == "delivery":
            title = f"TaskCenter 任务通知投递失败：{task.name}"
            streak_line = f"- 连续投递失败：{metrics.get('delivery_failure_streak', 0)} 次\n"
        else:
            title = f"TaskCenter 任务失败：{task.name}"
            streak_line = f"- 连续失败：{metrics.get('consecutive_failures', 0)} 次\n"
        content = (
            f"- 任务ID：{task.id}\n"
            f"- 任务名称：{task.name}\n"
            f"- Trace：{trace_id or '-'}\n"
            f"{streak_line}"
            f"- 错误原因：{error}\n"
            f"- 时间：{_now_iso()}"
        )

        channels = channels_from_task_config(_task_config(task))
        external_channels = [c for c in channels if c != "portal"] or ["dingtalk"]
        external_senders = {
            "dingtalk": NotificationService.send_dingtalk,
            "wechat_work": NotificationService.send_wechat_work,
            "email": NotificationService.send_email,
        }

        alerted = False
        async with AsyncSessionLocal() as db:
            try:
                await PortalNotificationService.create(
                    db,
                    user_id=user_id,
                    title=title,
                    content=content,
                    level="error",
                    category="task_center",
                    resource_type="scheduled_task",
                    resource_id=str(task.id),
                    metadata={"task_id": task.id, "task_name": task.name, "trace_id": trace_id, "alert_kind": kind},
                )
                await db.commit()
                alerted = True
            except Exception as portal_err:
                logger.warning("Task alert portal delivery failed for task %s: %s", task.id, portal_err)

            for channel in external_channels:
                sender = external_senders.get(channel)
                if sender is None:
                    continue
                ok, send_err = await sender(db, user_id, title, content)
                if ok:
                    alerted = True
                else:
                    logger.warning(
                        "Task alert delivery failed for task %s via %s: %s", task.id, channel, send_err
                    )

        if not alerted:
            return

        metrics["last_alert_at"] = _now_iso()
        async with AsyncSessionLocal() as db:
            latest = (await db.execute(select(AgentScheduledTask).where(AgentScheduledTask.id == task.id))).scalar_one_or_none()
            if latest:
                await _write_task_metrics(db, latest, metrics)
    except Exception as alert_err:
        logger.warning("Task failure alert failed for task %s: %s", task.id, alert_err, exc_info=True)


def _schedule_task_retry(task_id: int, retry_attempt: int, delay_sec: int) -> bool:
    """失败后安排一次性延迟重试；调度器不可用时降级为进程内延迟协程。"""
    from apscheduler.triggers.date import DateTrigger
    from datetime import timedelta

    scheduler = scheduler_service._scheduler
    if scheduler and scheduler.running:
        run_at = datetime.now(scheduler.timezone) + timedelta(seconds=delay_sec)
        scheduler.add_job(
            _scheduled_task_wrapper,
            DateTrigger(run_date=run_at),
            id=f"task_{task_id}_retry_{retry_attempt}",
            args=[task_id],
            kwargs={"retry_attempt": retry_attempt},
            replace_existing=True,
            misfire_grace_time=3600,
        )
        return True

    async def _delayed_retry():
        await asyncio.sleep(delay_sec)
        await _scheduled_task_wrapper(task_id, retry_attempt=retry_attempt)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    asyncio.create_task(_delayed_retry())
    return True


async def _handle_task_execution_failure(
    session: AsyncSession,
    task: AgentScheduledTask,
    *,
    trace_id: Optional[str],
    error: str,
    is_manual: bool,
    retry_attempt: int,
    task_config: Dict[str, Any],
) -> None:
    """统一失败处理：写失败指标 → 有重试额度则安排重试（并暂缓告警）→ 否则按节流告警。"""
    metrics = await _mark_task_failure(session, task, trace_id=trace_id, error=error)

    max_retries, retry_delay = retry_policy_from_task_config(task_config)
    if not is_manual and retry_attempt < max_retries:
        next_attempt = retry_attempt + 1
        if _schedule_task_retry(task.id, next_attempt, retry_delay):
            metrics["last_message"] = (
                f"任务执行失败，{retry_delay} 秒后自动重试（第 {next_attempt}/{max_retries} 次）"
            )
            await _write_task_metrics(session, task, metrics)
            logger.info(
                "🔁 Task %s retry %s/%s scheduled in %ss.", task.id, next_attempt, max_retries, retry_delay
            )
            return

    if _should_alert_failure(metrics):
        await _send_task_failure_alert(
            task.user_id, task, trace_id=trace_id, error=error, metrics=metrics
        )


async def _scheduled_task_wrapper(task_id: int, is_manual: bool = False, retry_attempt: int = 0):
    """
    Top-level wrapper function for task execution to avoid APScheduler serialization issues.
    """
    # Delay import to avoid circular dependencies
    from app.services.ai.agent_service import agent_service

    logger.info(f"🔔 Triggering {'MANUAL ' if is_manual else 'SCHEDULED '}task {task_id} (attempt={retry_attempt})")

    async with AsyncSessionLocal() as session:
        # 1. Fetch Task Details
        stmt = select(AgentScheduledTask).where(AgentScheduledTask.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        # If manual, we allow running even if paused (status=0)
        if not task or (task.status != 1 and not is_manual):
            logger.warning(f"⏩ Task {task_id} skipped: Not found or not active (Status: {task.status if task else 'N/A'}).")
            return

        task_config = _task_config(task)
        execution_timeout = execution_timeout_from_task_config(task_config)

        # 2. 执行期互斥锁：执行期间持有，覆盖手动与定时触发，跨节点互斥
        lock_token = await _acquire_task_execution_lock(
            task_id, execution_timeout + _TASK_EXEC_LOCK_TTL_BUFFER_SEC
        )
        if lock_token is None:
            logger.warning(f"⏩ Task {task_id} skipped: an execution is already in progress (locked).")
            await _mark_task_skipped(session, task, reason="任务正在执行中，本次触发已跳过")
            return

        try:
            # 3. User Impersonation
            user_stmt = select(User).where(User.id == task.user_id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()

            if not user:
                logger.error(f"Task {task_id} failed: User {task.user_id} not found.")
                metrics = await _mark_task_failure(
                    session,
                    task,
                    trace_id=None,
                    error=f"任务用户不存在：{task.user_id}",
                )
                if _should_alert_failure(metrics):
                    await _send_task_failure_alert(task.user_id, task, trace_id=None, error=f"任务用户不存在：{task.user_id}", metrics=metrics)
                return

            # 3.1 Fetch Agent Name for Forced Routing
            from app.models.agent import AIAgent
            agent_stmt = select(AIAgent.display_name).where(AIAgent.id == task.agent_id)
            agent_res = await session.execute(agent_stmt)
            agent_display_name = agent_res.scalar_one_or_none() or task.agent_id

            user_info = {
                "user_id": user.id,
                "user_name": user.user_name,
                "real_name": user.real_name,
                "role": user.role,
                "is_scheduled_task": True,
                "quick_suggestions_forbidden": True,
                "task_name": task.name,
                "requires_tool_execution": True,
            }

            from app.services.task_notification_channels import channels_from_task_config
            from app.services.task_execution_options import (
                debug_options_from_task_config,
                knowledge_dataset_ids_from_scope,
                metadata_dataset_ids_from_scope,
                permission_options_from_task_config,
                resource_scope_from_task_config,
            )

            # 4. Execute via Agent Service
            try:
                await _mark_task_attempt_started(session, task)

                resource_scope = resource_scope_from_task_config(task_config)
                debug_options = debug_options_from_task_config(task_config)
                knowledge_ids = knowledge_dataset_ids_from_scope(resource_scope)
                metadata_ids = metadata_dataset_ids_from_scope(resource_scope)

                full_prompt = _build_scheduled_task_prompt(
                    task_id,
                    agent_display_name,
                    task.prompt,
                    notification_channels=channels_from_task_config(task_config),
                )
                run_conversation_id = _new_task_run_conversation_id(task.conversation_id)

                logger.info(
                    "🚀 Executing task %s ('%s') | Agent: %s | TaskConvID: %s | RunConvID: %s | Timeout: %ss",
                    task_id,
                    task.name,
                    task.agent_id,
                    task.conversation_id,
                    run_conversation_id,
                    execution_timeout,
                )

                # NOTE: We don't generate trace_id here, we let agent_service generate it
                # and capture it from the response to ensure consistency with Audit Logs.
                try:
                    result = await asyncio.wait_for(
                        agent_service.chat_completion(
                            messages=[{"role": "user", "content": full_prompt}],
                            agent_id=task.agent_id,
                            conversation_id=run_conversation_id,
                            user_info=user_info,
                            enable_multi_agent=True,
                            debug_options=debug_options,
                            permission_options=permission_options_from_task_config(task_config),
                            knowledge_dataset_ids=knowledge_ids or None,
                            metadata_dataset_ids=metadata_ids or None,
                        ),
                        timeout=execution_timeout,
                    )
                except asyncio.TimeoutError:
                    raise TimeoutError(f"任务执行超时（超过 {execution_timeout} 秒），本次运行已中断")

                trace_id = result.get('trace_id')
                content_preview = result.get('content', '')[:100]
                logger.info(f"✅ Task {task_id} finished. Trace: {trace_id}. Response: {content_preview}...")

                if _is_incomplete_task_result(result):
                    error = _task_result_error(result)
                    logger.warning(
                        "⏸️ Task %s skipped run metadata update because execution did not complete. status=%s trace=%s error=%s",
                        task_id,
                        result.get("status"),
                        trace_id,
                        error,
                    )
                    if _is_busy_task_result(result):
                        await _mark_task_skipped(session, task, reason=error)
                    else:
                        await _handle_task_execution_failure(
                            session,
                            task,
                            trace_id=trace_id,
                            error=error,
                            is_manual=is_manual,
                            retry_attempt=retry_attempt,
                            task_config=task_config,
                        )
                    return

                # 5. Update Task Metadata (Atomic update)
                # 执行成功先落库，结果通知投递单独记录，投递失败不再把任务标失败。
                await session.execute(
                    update(AgentScheduledTask)
                    .where(AgentScheduledTask.id == task_id)
                    .values(
                        last_run_id=trace_id,
                        last_run_at=datetime.now(),
                        run_count=AgentScheduledTask.run_count + 1
                    )
                )
                await session.commit()
                await _mark_task_success(
                    session,
                    task,
                    trace_id=trace_id,
                    message="任务执行成功",
                )
                logger.info(f"📊 Updated run_count and last_run_id for task {task_id}")

            except Exception as e:
                logger.error(f"❌ Task {task_id} execution failed: {e}", exc_info=True)
                await _handle_task_execution_failure(
                    session,
                    task,
                    trace_id=None,
                    error=str(e),
                    is_manual=is_manual,
                    retry_attempt=retry_attempt,
                    task_config=task_config,
                )
                return

            # 6. 结果通知投递（与任务执行状态解耦）
            notification_channels = channels_from_task_config(task_config)
            if notification_channels:
                delivery_ok = False
                delivery_notes: List[str] = []
                try:
                    from app.services.task_notification_delivery import ensure_task_notification_deliveries

                    await asyncio.sleep(0.5)
                    delivery_ok, delivery_notes = await ensure_task_notification_deliveries(
                        session,
                        user_id=task.user_id,
                        task_name=task.name,
                        channels=notification_channels,
                        trace_id=trace_id,
                        content=str(result.get("content") or ""),
                        reasoning_content=str(result.get("reasoning_content") or "") or None,
                    )
                except Exception as delivery_err:
                    logger.error(f"❌ Task {task_id} notification delivery raised: {delivery_err}", exc_info=True)
                    delivery_notes = [str(delivery_err)]
                logger.info(
                    "📬 Task %s notification delivery trace=%s ok=%s notes=%s",
                    task_id,
                    trace_id,
                    delivery_ok,
                    delivery_notes,
                )
                delivery_metrics = await _record_task_delivery_result(
                    session, task, ok=delivery_ok, notes=delivery_notes
                )
                if not delivery_ok and _should_alert_delivery_failure(delivery_metrics):
                    error = "任务执行成功，但结果通知投递失败：" + "; ".join(delivery_notes)
                    await _send_task_failure_alert(
                        task.user_id, task, trace_id=trace_id, error=error,
                        metrics=delivery_metrics, kind="delivery",
                    )

            # Allow logs to flush
            await asyncio.sleep(0.5)
        finally:
            await _release_task_execution_lock(task_id, lock_token)


async def _system_audit_log_maintenance_job():
    """
    System-level background job to auto-expand partitions and prune expired logs.
    """
    logger.info("⏰ Starting system audit log partition maintenance job...")
    try:
        from app.services.partition_service import PartitionService
        from app.services.config_service import ConfigService
        
        async with AsyncSessionLocal() as db_session:
            # 1. 自动扩容未来分区
            await PartitionService.expand_partitions(db_session)
            
            # 2. 自动清理过期日志
            retention_str = await ConfigService.get("audit_log_retention_days", default="90")
            try:
                retention_days = int(retention_str)
            except (ValueError, TypeError):
                retention_days = 90
                
            res = await PartitionService.prune_expired_logs(db_session, retention_days)
            logger.info(f"✅ System audit log partition maintenance completed. Result: {res}")
    except Exception as e:
        logger.error(f"❌ Failed to run system audit log partition maintenance: {e}", exc_info=True)


async def _system_memory_consolidation_job():
    """
    系统级定时任务：每天凌晨对所有活跃用户的相似记忆进行合并整理与降噪。
    """
    logger.info("⏰ Starting system memory consolidation job...")
    
    # 1. 分布式锁 (ex=3600 nx=True)
    lock_key = f"lock:system_memory_consolidation:{datetime.now().strftime('%Y%m%d%H%M')}"
    if not await redis.redis_client.set(lock_key, "locked", ex=3600, nx=True):
        logger.warning("⏩ System memory consolidation skipped: lock already acquired by another node.")
        return
        
    try:
        from app.services.ai.memory_index_service import MemoryIndexService
        
        # 2. 查询所有启用的用户
        async with AsyncSessionLocal() as session:
            stmt = select(User.id).where(User.status == 1)
            result = await session.execute(stmt)
            user_ids = result.scalars().all()
            
        logger.info(f"Loaded {len(user_ids)} active users for memory consolidation.")
        
        # 3. 逐个用户执行记忆降噪合并
        for u_id in user_ids:
            try:
                # 传入 str(u_id) 因为记忆是以 string 作为 user_id 键存储的
                await MemoryIndexService.consolidate_user_memories(str(u_id))
            except Exception as ex:
                logger.error(f"❌ Failed to consolidate memory for user {u_id}: {ex}")
                
        logger.info("✅ System memory consolidation job finished successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to run system memory consolidation job: {e}", exc_info=True)


async def _system_knowledge_metrics_sync_job():
    """
    系统级定时任务：每天凌晨 3:30 运行知识库及文档被引用和检索指标同步落库。
    """
    logger.info("⏰ Starting system knowledge metrics sync job...")
    
    lock_key = f"lock:system_knowledge_metrics_sync:{datetime.now().strftime('%Y%m%d%H%M')}"
    try:
        if not await redis.redis_client.set(lock_key, "locked", ex=300, nx=True):
            logger.warning("⏩ System knowledge metrics sync skipped: lock already acquired by another node.")
            return
    except Exception as lock_err:
        logger.warning(f"Failed to acquire redis lock: {lock_err}")
        
    try:
        from app.services.knowledge_metrics_service import KnowledgeMetricsService
        await KnowledgeMetricsService.sync_redis_metrics_to_db()
        logger.info("✅ System knowledge metrics sync job finished successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to run system knowledge metrics sync job: {e}", exc_info=True)


async def _system_third_party_user_sync_job():
    """系统级定时任务：从第三方数据源同步用户。"""
    logger.info("⏰ Starting third-party user sync job...")

    lock_key = f"lock:system_third_party_user_sync:{datetime.now().strftime('%Y%m%d%H%M')}"
    if not await redis.redis_client.set(lock_key, "locked", ex=3600, nx=True):
        logger.warning("⏩ Third-party user sync skipped: lock already acquired by another node.")
        return

    try:
        from app.services.user_sync_service import UserSyncService

        config = await UserSyncService.get_config()
        if not config.enabled or config.schedule == "off":
            logger.info("Third-party user sync is disabled, skipping.")
            return

        async with AsyncSessionLocal() as session:
            result = await UserSyncService.run_sync(session)
            logger.info(
                "✅ Third-party user sync finished. created=%s updated=%s failed=%s",
                result["created"],
                result["updated"],
                result["failed"],
            )
    except Exception as e:
        logger.error(f"❌ Failed to run third-party user sync job: {e}", exc_info=True)


async def _system_scheduler_reconcile_job():
    """从共享任务库对账，接收关闭本地 scheduler 的 API 节点写入。"""
    try:
        await scheduler_service.reload_tasks()
        await scheduler_service.reload_saved_report_subscriptions()
        logger.info("🔄 Scheduler task definitions reconciled from database.")
    except Exception as exc:
        logger.warning("Failed to reconcile scheduler task definitions: %s", exc, exc_info=True)


async def _saved_report_subscription_wrapper(subscription_id: int, is_manual: bool = False):
    from app.api.portal.endpoints.saved_reports import ExecuteReportRequest, _execute_saved_report_impl
    from app.services.portal_notification_service import PortalNotificationService
    from app.services.notification_service import NotificationService
    from app.services.saved_report_digest_service import (
        build_deterministic_digest,
        enrich_digest_with_ai,
        render_mobile_markdown,
    )
    trigger_label = "手动触发" if is_manual else "定时触发"

    async def send_external(db, subscription, user_id, title, content, *, failure: bool):
        if failure and not (subscription.consecutive_failures == 1 or subscription.consecutive_failures % 3 == 0):
            return
        senders = {
            "dingtalk": NotificationService.send_dingtalk,
            "wechat_work": NotificationService.send_wechat_work,
            "email": NotificationService.send_email,
        }
        for channel in subscription.external_channels or []:
            sender = senders.get(channel)
            if sender:
                ok, error = await sender(db, user_id, title, content)
                if not ok:
                    logger.warning("Saved report external notification failed: channel=%s error=%s", channel, error)

    async with AsyncSessionLocal() as db:
        subscription = (await db.execute(select(PortalSavedReportSubscription).where(
            PortalSavedReportSubscription.id == subscription_id
        ))).scalar_one_or_none()
        if subscription is None or (subscription.status != "active" and not is_manual):
            return
        report = (await db.execute(select(PortalSavedReport).where(
            PortalSavedReport.id == subscription.report_id
        ))).scalar_one_or_none()
        user = (await db.execute(select(User).where(User.id == subscription.user_id))).scalar_one_or_none()
        if report is None or user is None or user.status != 1 or int(report.owner_user_id) != int(subscription.user_id):
            subscription.status = "error"
            subscription.last_error = "报表所有权或订阅用户状态已失效"
            await db.commit()
            return

        user_info = {
            "user_id": str(user.id), "user_name": user.user_name, "real_name": user.real_name,
            "role": user.role, "dept_code": user.dept_code, "org_path": user.org_path,
            "extra_data": user.extra_data,
            "is_subscription_task": True,
            "quick_suggestions_forbidden": True,
        }
        try:
            await _execute_saved_report_impl(
                report.id, ExecuteReportRequest(params=subscription.params or {}), None, user_info, db,
                trigger_type="scheduled", task_id=subscription.id,
            )
            run = (await db.execute(select(PortalSavedReportRun).where(
                PortalSavedReportRun.report_id == report.id,
                PortalSavedReportRun.task_id == subscription.id,
            ).order_by(PortalSavedReportRun.id.desc()).limit(1))).scalar_one_or_none()
            subscription.last_run_id = run.id if run else None
            subscription.last_run_at = datetime.now()
            subscription.consecutive_failures = 0
            subscription.last_error = None
            from app.services.saved_report_subscription_service import evaluate_alert_condition
            alert_evaluation = evaluate_alert_condition(
                getattr(subscription, "alert_condition", None),
                run.result_snapshot if run else None,
                getattr(subscription, "alert_state", None),
            )
            subscription.alert_state = alert_evaluation.next_state
            if subscription.notify_on_success and alert_evaluation.hit:
                try:
                    async with db.begin_nested():
                        digest = build_deterministic_digest(report, run, subscription.params or {})
                        digest = await enrich_digest_with_ai(
                            digest,
                            enabled=bool(getattr(subscription, "ai_analysis_enabled", True)),
                            analysis_instruction=getattr(subscription, "analysis_instruction", None),
                        )
                        public_base = str(settings.APP_PUBLIC_URL or "").rstrip("/")
                        report_url = (
                            f"{public_base}/dashboard/chat?dataset_portal=1&report_id={report.id}&run_id={run.id}"
                            if public_base and run else ""
                        )
                        title, content = render_mobile_markdown(digest, report_url, "inbox")
                        await PortalNotificationService.create(
                            db, user_id=user.id, title=title, content=content, level="success",
                            resource_type="saved_report_run", resource_id=str(run.id) if run else None,
                            metadata={
                                "report_id": report.id,
                                "report_title": report.title,
                                "subscription_id": subscription.id,
                                "digest_mode": digest.get("generation_mode"),
                                "trigger_evidence": alert_evaluation.evidence,
                            },
                        )
                        if run:
                            db.add(PortalSavedReportDigestDelivery(
                                run_id=run.id, subscription_id=subscription.id, channel="inbox",
                                digest_payload=digest, trigger_evidence=alert_evaluation.evidence,
                                title=title, content=content, status="success",
                                ai_status=digest.get("ai_status") or "disabled", sent_at=datetime.now(),
                            ))
                        senders = {
                            "dingtalk": NotificationService.send_dingtalk,
                            "wechat_work": NotificationService.send_wechat_work,
                            "email": NotificationService.send_email,
                        }
                        for channel in subscription.external_channels or []:
                            sender = senders.get(channel)
                            if sender is None:
                                continue
                            channel_title, channel_content = render_mobile_markdown(digest, report_url, channel)
                            ok, error = await sender(db, user.id, channel_title, channel_content)
                            if run:
                                db.add(PortalSavedReportDigestDelivery(
                                    run_id=run.id, subscription_id=subscription.id, channel=channel,
                                    digest_payload=digest, trigger_evidence=alert_evaluation.evidence,
                                    title=channel_title, content=channel_content,
                                    status="success" if ok else "failed",
                                    error_message=None if ok else error,
                                    ai_status=digest.get("ai_status") or "disabled",
                                    sent_at=datetime.now(),
                                ))
                            if not ok:
                                logger.warning("Saved report digest delivery failed: channel=%s error=%s", channel, error)
                except Exception as digest_error:
                    logger.exception("Saved report digest generation or audit failed: %s", type(digest_error).__name__)
            await db.commit()
        except Exception as exc:
            subscription.last_run_at = datetime.now()
            subscription.consecutive_failures = int(subscription.consecutive_failures or 0) + 1
            subscription.last_error = str(getattr(exc, "detail", exc))[:10000]
            run = (await db.execute(select(PortalSavedReportRun).where(
                PortalSavedReportRun.report_id == report.id,
                PortalSavedReportRun.task_id == subscription.id,
            ).order_by(PortalSavedReportRun.id.desc()).limit(1))).scalar_one_or_none()
            subscription.last_run_id = run.id if run else None
            title = f"报表运行失败：{report.title}"
            failure_content = f"触发方式：{trigger_label}\n{subscription.last_error}"
            await PortalNotificationService.create(
                db, user_id=user.id, title=title,
                content=failure_content, level="error", resource_type="saved_report_run",
                resource_id=str(run.id) if run else None,
                metadata={"report_id": report.id, "report_title": report.title, "subscription_id": subscription.id},
            )
            if subscription.notify_on_failure:
                await send_external(db, subscription, user.id, title, failure_content, failure=True)
            await db.commit()


class TaskSchedulerService:
    _instance = None
    _scheduler: Optional[AsyncIOScheduler] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TaskSchedulerService, cls).__new__(cls)
        return cls._instance

    async def start(self):
        if self._scheduler and self._scheduler.running:
            return

        db_url = settings.DATABASE_SYNC_URL
        # Use a custom table name to ensure a clean slate and match project conventions
        job_stores = {
            'default': SQLAlchemyJobStore(url=db_url, tablename='ai_agent_scheduler_jobs')
        }
        from pytz import timezone
        from app.services.platform_timezone import get_cached_platform_timezone, refresh_platform_timezone

        try:
            await refresh_platform_timezone()
        except Exception as exc:
            logger.warning("Failed to refresh platform timezone before scheduler start: %s", exc)

        tz = timezone(get_cached_platform_timezone())

        self._scheduler = AsyncIOScheduler(jobstores=job_stores, timezone=tz)
        self._scheduler.start()

        # 注册系统日志与分区清理常驻任务，每日凌晨 2:00 运行
        self._scheduler.add_job(
            _system_audit_log_maintenance_job,
            CronTrigger(hour=2, minute=0, timezone=tz),
            id="system_audit_log_maintenance",
            replace_existing=True
        )

        # 注册系统记忆降噪合并任务，每日凌晨 3:00 运行
        self._scheduler.add_job(
            _system_memory_consolidation_job,
            CronTrigger(hour=3, minute=0, timezone=tz),
            id="system_memory_consolidation",
            replace_existing=True
        )

        # 注册知识库运营数据归并同步任务，每日凌晨 3:30 运行
        self._scheduler.add_job(
            _system_knowledge_metrics_sync_job,
            CronTrigger(hour=3, minute=30, timezone=tz),
            id="system_knowledge_metrics_sync",
            replace_existing=True
        )

        # API 节点可能关闭本地调度器；由唯一的启用节点定期从任务库对账，
        # 让新增、修改、停用和删除在不重启调度节点的情况下最终生效。
        self._scheduler.add_job(
            _system_scheduler_reconcile_job,
            IntervalTrigger(seconds=SCHEDULER_RECONCILE_INTERVAL_SEC, timezone=tz),
            id="system_scheduler_reconcile",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

        await self.reschedule_third_party_user_sync()

        now = datetime.now(tz)
        logger.info(
            "🚀 Agent Task Scheduler started (tz=%s). Current Scheduler Time: %s",
            get_cached_platform_timezone(),
            now,
        )
        await self.reload_tasks()
        await self.reload_saved_report_subscriptions()
        await self._cleanup_stale_running_tasks()

    async def _cleanup_stale_running_tasks(self):
        """启动时清理残留的 running 状态：进程崩溃/重启会让 metrics 永远停在「正在执行」。

        若 Redis 执行锁仍被其他节点持有则视为真在执行，跳过。
        """
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(AgentScheduledTask))
                tasks = result.scalars().all()
                cleaned = 0
                for task in tasks:
                    metrics = _task_metrics(task)
                    if metrics.get("last_status") != "running":
                        continue
                    if redis.redis_client is not None:
                        try:
                            if await redis.redis_client.get(_task_execution_lock_key(task.id)):
                                continue  # 其他节点正在执行
                        except Exception:
                            pass
                    metrics["last_status"] = "failed"
                    metrics["last_message"] = "执行被中断"
                    metrics["last_error"] = "检测到服务重启，上一次执行未正常结束"
                    metrics["last_finished_at"] = _now_iso()
                    await _write_task_metrics(session, task, metrics)
                    cleaned += 1
                if cleaned:
                    logger.info("🧹 Cleaned up %s stale 'running' task metric(s) after restart.", cleaned)
        except Exception as exc:
            logger.warning("Failed to clean up stale running tasks: %s", exc, exc_info=True)

    async def stop(self):
        if self._scheduler:
            if self._scheduler.running:
                self._scheduler.shutdown()
            self._scheduler = None
            logger.info("🛑 Agent Task Scheduler stopped.")

    async def apply_platform_timezone_change(self) -> None:
        """Reboot scheduler so Cron jobs pick up the new platform timezone."""
        if not self._scheduler:
            return
        logger.info(
            "♻️ Reloading scheduler after platform timezone change → %s",
            get_cached_platform_timezone(),
        )
        await self.stop()
        await self.start()

    async def reload_tasks(self):
        if not self._scheduler:
            return

        active_task_ids = set()
        async with AsyncSessionLocal() as session:
            stmt = select(AgentScheduledTask).where(AgentScheduledTask.status == 1)
            result = await session.execute(stmt)
            tasks = result.scalars().all()
            for task in tasks:
                active_task_ids.add(task.id)
                await self._add_job_to_memory(task)

        for job in list(self._scheduler.get_jobs()):
            task_id = _task_id_from_job_id(job.id)
            if task_id is not None and task_id not in active_task_ids:
                self._scheduler.remove_job(job.id)
        logger.info(f"Loaded {len(tasks)} active tasks into scheduler.")

    async def reload_saved_report_subscriptions(self):
        if not self._scheduler:
            return

        active_subscription_ids = set()
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(PortalSavedReportSubscription).where(
                PortalSavedReportSubscription.status == "active"
            ))
            subscriptions = result.scalars().all()
            for subscription in subscriptions:
                active_subscription_ids.add(subscription.id)
                await self.upsert_saved_report_subscription(subscription)

        for job in list(self._scheduler.get_jobs()):
            subscription_id = _saved_report_subscription_id_from_job_id(job.id)
            if subscription_id is not None and subscription_id not in active_subscription_ids:
                self._scheduler.remove_job(job.id)
        logger.info("Loaded %s active saved report subscriptions into scheduler.", len(subscriptions))

    async def upsert_saved_report_subscription(self, subscription: PortalSavedReportSubscription):
        if not self._scheduler:
            return
        job_id = f"saved_report_subscription_{subscription.id}"
        trigger = CronTrigger.from_crontab(
            subscription.cron_expr,
            timezone=subscription.timezone or get_cached_platform_timezone(),
        )
        existing_job = self._scheduler.get_job(job_id)
        if existing_job and subscription.status == "active" and repr(existing_job.trigger) == repr(trigger):
            return
        if existing_job:
            self._scheduler.remove_job(job_id)
        if subscription.status == "active":
            self._scheduler.add_job(
                _saved_report_subscription_wrapper,
                trigger,
                id=job_id, args=[subscription.id], replace_existing=True, misfire_grace_time=3600,
            )

    async def remove_saved_report_subscription(self, subscription_id: int):
        if not self._scheduler:
            return
        job_id = f"saved_report_subscription_{subscription_id}"
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

    def get_saved_report_subscription_next_run_time(self, subscription_id: int) -> Optional[datetime]:
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(f"saved_report_subscription_{subscription_id}")
        return job.next_run_time if job else None

    async def _add_job_to_memory(self, task: AgentScheduledTask):
        if not self._scheduler:
            return
            
        job_id = f"task_{task.id}"
        try:
            trigger = CronTrigger.from_crontab(
                task.cron_expr,
                timezone=get_cached_platform_timezone(),
            )
        except (ValueError, KeyError) as e:
            logger.error(
                f"❌ Task {task.id} ('{task.name}') has invalid cron_expr '{task.cron_expr}': {e}. "
                f"Skipping scheduling. Please fix the cron expression in the task configuration."
            )
            return

        existing_job = self._scheduler.get_job(job_id)
        if existing_job and repr(existing_job.trigger) == repr(trigger):
            return

        # Defensive cleanup: remove if exists
        if existing_job:
            self._scheduler.remove_job(job_id)
            logger.info(f"Removed stale job {job_id} from memory")

        try:
            # Use top-level wrapper function
            self._scheduler.add_job(
                _scheduled_task_wrapper,
                trigger,
                id=job_id,
                args=[task.id],
                replace_existing=True,
                misfire_grace_time=3600
            )
            next_run = self.get_next_run_time(task.id)
            logger.info(f"✅ Successfully scheduled task {task.id} ('{task.name}'). Next run: {next_run}")
        except Exception as e:
            logger.error(f"❌ Failed to schedule task {task.id}: {e}", exc_info=True)

    async def run_task(self, task_id: int, is_manual: bool = False):
        """External entry point for manual triggering."""
        await _scheduled_task_wrapper(task_id, is_manual=is_manual)

    async def upsert_task(self, task: AgentScheduledTask):
        if not self._scheduler:
            logger.warning(f"⚠️ Scheduler not running, skipping upsert for task {task.id}")
            return

        if task.status == 1:
            await self._add_job_to_memory(task)
        else:
            job_id = f"task_{task.id}"
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)

    async def remove_task(self, task_id: int):
        """彻底摘除任务的调度 Job（含未执行的重试 Job），用于删除任务/删除用户。"""
        if not self._scheduler:
            return
        job_ids = [f"task_{task_id}"] + [
            f"task_{task_id}_retry_{attempt}" for attempt in range(1, MAX_TASK_RETRIES + 1)
        ]
        for job_id in job_ids:
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)

    def get_next_run_time(self, task_id: int) -> Optional[datetime]:
        if not self._scheduler:
            return None
        job = self._scheduler.get_job(f"task_{task_id}")
        return job.next_run_time if job else None

    async def reschedule_third_party_user_sync(self, config=None):
        """根据第三方用户同步配置注册/移除定时任务。"""
        if not self._scheduler:
            return

        from pytz import timezone
        from app.services.user_sync_service import UserSyncService

        tz = timezone(get_cached_platform_timezone())
        job_id = "system_third_party_user_sync"

        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

        cfg = config or await UserSyncService.get_config()
        if not cfg.enabled or cfg.schedule == "off":
            logger.info("Third-party user sync scheduler: disabled")
            return

        cron_kwargs = UserSyncService.schedule_to_cron(cfg.schedule)
        if not cron_kwargs:
            return

        self._scheduler.add_job(
            _system_third_party_user_sync_job,
            CronTrigger(timezone=tz, **cron_kwargs),
            id=job_id,
            replace_existing=True,
        )
        logger.info("Third-party user sync scheduler registered: schedule=%s", cfg.schedule)

scheduler_service = TaskSchedulerService()
