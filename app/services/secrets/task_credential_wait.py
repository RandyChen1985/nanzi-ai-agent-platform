"""定时任务的凭据暂停记录。执行快照仍由 AgentScope 挂起存储管理。"""

from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json

from sqlalchemy import and_, or_, select, update

from app.models.credential_type import TaskCredentialWait
from app.models.secret import SecretAuditLog
from app.models.task import AgentScheduledTask
from app.services.secrets.credential_approval import CredentialApproval, approval_claim_id
from app.services.secrets.credential_schema import schema_digest
from app.services.secrets.type_catalog import audit_change

TASK_CREDENTIAL_REFS_KEY = "_mcp_credential_refs"


@dataclass(frozen=True, repr=False)
class TaskCredentialRef:
    task_id: int
    actor_id: str
    profile_id: str
    revision: int
    secret_id: str


current_task_credential_refs: ContextVar[dict[str, TaskCredentialRef | None] | None] = ContextVar(
    "current_task_credential_refs", default=None,
)


def public_task_config(config: dict | None) -> dict | None:
    if isinstance(config, dict):
        return {key: value for key, value in config.items() if key != TASK_CREDENTIAL_REFS_KEY}
    return config


async def load_task_credential_refs(session, task) -> dict[str, TaskCredentialRef | None]:
    config = task.config if isinstance(task.config, dict) else {}
    refs = config.get(TASK_CREDENTIAL_REFS_KEY)
    if not isinstance(refs, dict):
        return {}
    refs = {
        tool_id: receipt_id for tool_id, receipt_id in refs.items()
        if isinstance(tool_id, str) and isinstance(receipt_id, str) and len(receipt_id) == 36
    }
    if not refs:
        return {}
    receipts = (await session.scalars(select(SecretAuditLog).where(
        SecretAuditLog.id.in_(set(refs.values())),
        SecretAuditLog.actor_id == str(task.user_id),
        SecretAuditLog.action == "mcp_approval_claim",
    ))).all()
    choices: dict[str, TaskCredentialRef | None] = dict.fromkeys(refs)
    for receipt in receipts:
        try:
            detail = json.loads(receipt.detail or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(detail, dict):
            continue
        tool_id = detail.get("tool_id")
        request_id = detail.get("request_id")
        if (
            isinstance(tool_id, str) and refs.get(tool_id) == receipt.id
            and isinstance(request_id, str) and approval_claim_id(request_id) == receipt.id
            and detail.get("task_id") == task.id and isinstance(detail.get("profile_id"), str)
            and type(detail.get("revision")) is int and receipt.secret_id
        ):
            choices[tool_id] = TaskCredentialRef(
                task.id, receipt.actor_id, detail["profile_id"], detail["revision"], receipt.secret_id,
            )
    return choices


def task_credential_secret_id(ref: dict, actor_id: str) -> str | None:
    from app.services.secrets.mcp_profiles import CredentialInvocationError

    choices = current_task_credential_refs.get() or {}
    tool_id = ref.get("tool_id")
    if tool_id not in choices:
        return None
    choice = choices[tool_id]
    if choice is None:
        raise CredentialInvocationError("CREDENTIAL_PROFILE_CHANGED", "此任务原有的凭据选择已失效，请重新选择")
    if choice.actor_id != actor_id:
        return None
    if (choice.profile_id, choice.revision) != (ref.get("profile_id"), ref.get("revision")):
        raise CredentialInvocationError("CREDENTIAL_PROFILE_CHANGED", "工具凭据配置已变化，请重新选择")
    return choice.secret_id


async def record_task_credential_claim(
    session, approval: CredentialApproval, receipt_id: str,
) -> TaskCredentialRef | None:
    """同一事务写入授权凭证与任务引用，仅允许拥有者正在恢复的快照。"""
    task = await session.get(
        AgentScheduledTask, approval.scheduled_task_id, with_for_update=True, populate_existing=True,
    )
    if task is None or str(task.user_id) != approval.user_id:
        return None
    wait = await session.get(TaskCredentialWait, approval.request_id, with_for_update=True, populate_existing=True)
    if (
        wait is None or wait.task_id != task.id or str(wait.user_id) != approval.user_id
        or wait.run_conversation_id != approval.conversation_id or wait.status != "resuming"
        or wait.expires_at <= datetime.utcnow() or wait.task_definition_digest != task_definition_digest(task)
    ):
        return None
    config = dict(task.config or {})
    refs = config.get(TASK_CREDENTIAL_REFS_KEY)
    config[TASK_CREDENTIAL_REFS_KEY] = {
        **(refs if isinstance(refs, dict) else {}), approval.tool_id: receipt_id,
    }
    task.config = config
    return TaskCredentialRef(
        task.id, approval.user_id, approval.profile_id, approval.profile_revision, approval.secret_id,
    )


def remember_task_credential_for_run(tool_id: str, choice: TaskCredentialRef) -> None:
    choices = current_task_credential_refs.get()
    if choices is None:
        return
    previous = choices.get(tool_id)
    if previous is None or (previous.task_id, previous.actor_id) == (choice.task_id, choice.actor_id):
        # 恢复调用可能由子协程执行。原位更新仅属于本次任务的映射，父协程的后续调用也能读取。
        choices[tool_id] = choice


async def require_pending_task_wait(session, pending, ref: dict, user_id: str, *, lock: bool = False) -> None:
    from app.services.secrets.mcp_profiles import CredentialInvocationError

    task_id = ref.get("scheduled_task_id")
    if task_id is None:
        return
    task = await session.get(AgentScheduledTask, task_id, with_for_update=lock)
    wait = await session.get(TaskCredentialWait, pending.request_id, with_for_update=lock)
    if (
        task is None or wait is None or wait.task_id != task_id
        or str(task.user_id) != user_id or str(wait.user_id) != user_id
        or wait.run_conversation_id != pending.snapshot.conversation_id
    ):
        raise CredentialInvocationError("TASK_NOT_FOUND", "任务或授权不存在", 404)
    if wait.status == "expired" or wait.expires_at <= datetime.utcnow():
        raise CredentialInvocationError("TASK_CREDENTIAL_WAIT_EXPIRED", "任务授权已过期")
    if wait.status != "pending" or wait.task_definition_digest != task_definition_digest(task):
        raise CredentialInvocationError("TASK_CREDENTIAL_WAIT_CLOSED", "任务授权已处理或任务配置已变化")


def wait_event(chunk: dict) -> dict | None:
    if chunk.get("type") == "permission_required" and chunk.get("credential_selection_required"):
        request_id, expires = chunk.get("permission_request_id"), chunk.get("expires_at")
        if isinstance(request_id, str) and isinstance(expires, int):
            return {"request_id": request_id, "expires_at": expires}
    return None


async def expire_waits(session, task_id: int) -> None:
    # 状态查询入口也会回收租约；和任务修改/授权恢复统一先锁任务，再锁等待记录。
    await session.get(AgentScheduledTask, task_id, with_for_update=True)
    now = datetime.utcnow()
    await session.execute(update(TaskCredentialWait).where(
        TaskCredentialWait.task_id == task_id,
        TaskCredentialWait.status == "pending",
        TaskCredentialWait.expires_at <= now,
    ).values(status="expired", resolved_at=now))
    stalled = (await session.scalars(select(TaskCredentialWait).where(
        TaskCredentialWait.task_id == task_id, TaskCredentialWait.status == "resuming",
        or_(TaskCredentialWait.resume_lease_until.is_(None), TaskCredentialWait.resume_lease_until <= now),
    ).with_for_update())).all()
    for row in stalled:
        await recover_wait(session, row)
    await session.flush()


async def recover_wait(session, row) -> None:
    """调用方先锁任务、再锁等待记录；已消费的调用绝不重新开放。"""
    claim = await session.get(
        SecretAuditLog, approval_claim_id(row.request_id), with_for_update=True, populate_existing=True,
    )
    if claim is not None:
        resolve_wait(session, row, "failed")
    elif row.expires_at <= datetime.utcnow():
        resolve_wait(session, row, "expired")
    else:
        # 重读调用方已持有的任务锁，避开 ORM 缓存和可重复读事务中的旧快照。
        task = await session.get(
            AgentScheduledTask, row.task_id, with_for_update=True, populate_existing=True,
        )
        if (
            task is None or str(task.user_id) != str(row.user_id)
            or row.task_definition_digest != task_definition_digest(task)
        ):
            resolve_wait(session, row, "cancelled")
            return
        row.status = "pending"
        row.resume_lease_until = None


async def latest_wait(session, task_id: int, *, active_only: bool = False, for_update: bool = False):
    stmt = select(TaskCredentialWait).where(TaskCredentialWait.task_id == task_id)
    if active_only:
        now = datetime.utcnow()
        stmt = stmt.where(or_(
            and_(TaskCredentialWait.status == "pending", TaskCredentialWait.expires_at > now),
            and_(TaskCredentialWait.status == "resuming", TaskCredentialWait.resume_lease_until > now),
        ))
    stmt = stmt.order_by(TaskCredentialWait.created_at.desc()).limit(1)
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(stmt)


def task_definition_digest(task) -> str:
    config = public_task_config(task.config) or {}
    config.pop("task_metrics", None)
    return schema_digest([task.agent_id, task.conversation_id, task.prompt, task.cron_expr, task.status, config])


def add_wait(session, task, *, event: dict, trace_id: str, conversation_id: str):
    row = TaskCredentialWait(
        request_id=event["request_id"], task_id=task.id, user_id=task.user_id,
        run_conversation_id=conversation_id, trace_id=trace_id,
        task_definition_digest=task_definition_digest(task),
        expires_at=datetime.fromtimestamp(event["expires_at"], tz=timezone.utc).replace(tzinfo=None),
    )
    session.add(row)
    audit_change(session, "task_credential_wait", str(task.user_id), {
        "task_id": task.id, "request_id": row.request_id, "trace_id": trace_id,
    })
    return row


def resolve_wait(session, row, status: str) -> None:
    row.status = status
    row.resolved_at = datetime.utcnow()
    row.resume_lease_until = None
    audit_change(session, "task_credential_result", str(row.user_id), {
        "task_id": row.task_id, "request_id": row.request_id, "trace_id": row.trace_id, "status": status,
    })


def wait_view(row):
    if row is None:
        return None
    status = "expired" if row.status == "pending" and row.expires_at <= datetime.utcnow() else row.status
    return {
        "request_id": row.request_id, "trace_id": row.trace_id, "status": status,
        "expires_at": row.expires_at.replace(tzinfo=timezone.utc).isoformat(),
    }
