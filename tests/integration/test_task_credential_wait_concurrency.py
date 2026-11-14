"""MySQL/PostgreSQL 定时任务状态回归。使用按用例创建并销毁的独立数据库。"""

import asyncio
from datetime import datetime, timedelta
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.endpoints import tasks as task_api
from app.models.agent import AIAgent
from app.models.credential_type import TaskCredentialWait
from app.models.secret import SecretAuditLog
from app.models.task import AgentScheduledTask
from app.models.user import User
from app.services.ai import scheduler_service as scheduler
from app.services.secrets import credential_approval as approvals, mcp_profiles, task_credential_wait as waits
from app.services.task_center_service import TaskCenterService
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401

pytestmark = pytest.mark.no_infrastructure
OWNER = {"user_id": 101}
REQUEST = "perm_synthetic"
TRACE = "synthetic-trace"
RUN = "task_conversation_run_synthetic"


@pytest.fixture
async def sessions(monkeypatch):
    models = (AgentScheduledTask, TaskCredentialWait, SecretAuditLog, AIAgent, User)
    async with temporary_database(*(model.__table__ for model in models)) as engine:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        monkeypatch.setattr(scheduler, "AsyncSessionLocal", factory)
        monkeypatch.setattr(scheduler.scheduler_service, "upsert_task", AsyncMock())
        yield factory


async def seed_task(sessions, *, waiting=False, expired=False):
    async with sessions() as db:
        task = AgentScheduledTask(
            name="Test task", user_id=OWNER["user_id"], agent_id="synthetic-agent",
            conversation_id="task_conversation", cron_expr="0 * * * *", prompt="Original instructions", config={},
        )
        db.add(task)
        await db.flush()
        if waiting:
            waits.add_wait(
                db, task, event={"request_id": REQUEST, "expires_at": int(time.time()) + (-60 if expired else 120)},
                trace_id=TRACE, conversation_id=RUN,
            )
            task.config = {"task_metrics": {
                "trigger_count": 1, "last_status": "awaiting_credential", "last_trace_id": TRACE,
                "last_run_conversation_id": RUN, "credential_wait_request_id": REQUEST,
            }}
        await db.commit()
        return task.id


@pytest.mark.parametrize("delivery_ok", [True, False])
async def test_task_completion_updates_real_session_and_keeps_config_edit_during_delivery(sessions, monkeypatch, delivery_ok):
    from app.services import task_notification_delivery

    task_id = await seed_task(sessions)

    async def deliver(*args, **kwargs):
        async with sessions() as db:
            task = await db.get(AgentScheduledTask, task_id)
            assert task.run_count == 1 and task.config["task_metrics"]["last_status"] == "success"
            task.config = {**task.config, "task_constraints": "edited", "_mcp_credential_refs": {"tool": "ref"}}
            await db.commit()
        return delivery_ok, [] if delivery_ok else ["synthetic delivery failure"]

    monkeypatch.setattr(task_notification_delivery, "ensure_task_notification_deliveries", deliver)
    monkeypatch.setattr(scheduler, "_send_task_failure_alert", AsyncMock())
    async with sessions() as db:
        task = await db.get(AgentScheduledTask, task_id)
        await scheduler._mark_task_attempt_started(db, task, conversation_id=RUN)
        await scheduler._complete_task_execution(db, task, TRACE, {"content": "result"}, ["portal"])
        assert task.config["task_metrics"]["success_count"] == 1

    async with sessions() as db:
        task = await db.get(AgentScheduledTask, task_id)
        metrics = task.config["task_metrics"]
        assert task.run_count == 1
        assert task.last_run_id == TRACE
        assert task.config["task_constraints"] == "edited"
        assert task.config["_mcp_credential_refs"] == {"tool": "ref"}
        assert (metrics["trigger_count"], metrics["success_count"], metrics["failure_count"]) == (1, 1, 0)
        assert metrics["last_status"] == "success"
        assert metrics["last_delivery_status"] == ("success" if delivery_ok else "failed")
        assert metrics["delivery_failure_streak"] == (0 if delivery_ok else 1)


async def test_metrics_updates_merge_across_two_sessions(sessions):
    task_id = await seed_task(sessions)

    async with sessions() as older, sessions() as newer:
        stale = await older.get(AgentScheduledTask, task_id)
        active = await newer.get(AgentScheduledTask, task_id)
        await scheduler._mark_task_attempt_started(newer, active, conversation_id=RUN)
        await scheduler._mark_task_skipped(older, stale, reason="busy", already_running=True)

    async with sessions() as db:
        metrics = (await db.get(AgentScheduledTask, task_id)).config["task_metrics"]
        assert (metrics["trigger_count"], metrics["skipped_count"], metrics["last_status"]) == (1, 1, "running")


async def test_locked_wait_does_not_block_list_and_detail_reads(sessions, monkeypatch):
    task_id = await seed_task(sessions, waiting=True, expired=True)
    next_run = datetime(2030, 1, 1)
    monkeypatch.setattr(scheduler.scheduler_service, "get_next_run_time", lambda _: next_run)

    async def read():
        async with sessions() as db:
            listed = (await task_api.list_tasks(OWNER, db)).data
            detail = (await task_api.get_task(task_id, OWNER, db)).data
            wait = (await task_api.task_credential_wait_status(task_id, OWNER, db)).data
            assert listed[0].last_status == detail.last_status == "credential_expired"
            assert listed[0].next_run_at == detail.next_run_at == next_run
            assert wait["status"] == "expired"
            # FastAPI 的请求事务在返回时会 commit。
            await db.commit()

    async with sessions() as locked:
        await locked.get(AgentScheduledTask, task_id, with_for_update=True)
        await asyncio.wait_for(read(), timeout=2)
        await asyncio.wait_for(scheduler._maintain_task_credential_waits(), timeout=2)

    async with sessions() as db:
        assert (await db.get(TaskCredentialWait, REQUEST)).status == "pending"
    await scheduler._maintain_task_credential_waits()
    async with sessions() as db:
        assert (await db.get(TaskCredentialWait, REQUEST)).status == "expired"


@pytest.mark.parametrize("race", ["duplicate", "edit_before_claim", "edit_after_claim", "revoked"])
async def test_resume_serializes_edits_and_outbound_claim(sessions, monkeypatch, race):
    from app.services.ai.agent_service import agent_service

    task_id = await seed_task(sessions, waiting=True)
    user_id = str(OWNER["user_id"])
    snapshot = SimpleNamespace(
        request_id=REQUEST,
        user_id=user_id,
        snapshot=SimpleNamespace(
            trace_id=TRACE, conversation_id=RUN, created_at=time.time(), tool_call={"input": "{}"},
            runner_context={approvals.MCP_CREDENTIAL_REF: {
                "tool_id": "synthetic-tool", "profile_id": "synthetic-profile", "revision": 1,
                "scheduled_task_id": task_id,
            }},
        ),
    )
    monkeypatch.setattr(waits, "peek_task_credential_snapshot", AsyncMock(return_value=snapshot))
    discarded = AsyncMock()
    monkeypatch.setattr(waits, "discard_task_credential_snapshot", discarded)
    monkeypatch.setattr(scheduler, "_acquire_task_execution_lock", AsyncMock(return_value=""))
    monkeypatch.setattr(mcp_profiles.SecretResolver, "_platform_flags", AsyncMock(return_value=(False, True, 3)))
    monkeypatch.setattr(mcp_profiles, "pending_candidates", AsyncMock(return_value=[] if race == "revoked" else [object()]))
    ready = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def offline_resume(**kwargs):
        if race == "edit_before_claim":
            ready.set()
            await release.wait()
        async with sessions() as db:
            claim = approvals.approval_for_pending(snapshot, kwargs["credential_secret_id"])
            outbound = await approvals.claim_approval(db, claim)
        if outbound:
            calls.append(kwargs["permission_request_id"])
        if race != "edit_before_claim":
            ready.set()
            await release.wait()
        yield {"type": "run_status", "status": "success" if outbound else "error"}

    monkeypatch.setattr(agent_service, "resume_agentscope_permission_stream", offline_resume)

    async def resume():
        return await scheduler.resume_task_credential(task_id, REQUEST, "synthetic-secret", user_info=OWNER)

    if race == "revoked":
        with pytest.raises(mcp_profiles.CredentialInvocationError) as exc:
            await resume()
        assert exc.value.code == "CREDENTIAL_SELECTION_INVALID"
        assert not calls
        async with sessions() as db:
            assert (await db.get(TaskCredentialWait, REQUEST)).status == "pending"
        return

    first = asyncio.create_task(resume())
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        if race == "duplicate":
            with pytest.raises(mcp_profiles.CredentialInvocationError) as exc:
                await resume()
            assert exc.value.code == "TASK_CREDENTIAL_WAIT_CLOSED"
        else:
            async with sessions() as db:
                await TaskCenterService.update_task(db, task_id, {"prompt": "Edited instructions"})
            async with sessions() as db:
                row = await db.get(TaskCredentialWait, REQUEST)
                assert row.status == ("cancelled" if race == "edit_before_claim" else "resuming")
            if race == "edit_before_claim":
                discarded.assert_awaited_once_with(REQUEST, user_id)
            else:
                discarded.assert_not_awaited()
    finally:
        release.set()
        outcome = await asyncio.wait_for(first, timeout=3)

    assert outcome["status"] == ("cancelled" if race == "edit_before_claim" else "completed")
    assert calls == ([] if race == "edit_before_claim" else [REQUEST])
    async with sessions() as db:
        task = await db.get(AgentScheduledTask, task_id)
        claim = await db.get(SecretAuditLog, approvals.approval_claim_id(REQUEST))
        assert (claim is not None) == (race != "edit_before_claim")
        assert task.run_count == (1 if race == "duplicate" else 0)
        if race.startswith("edit_"):
            assert task.prompt == "Edited instructions"
