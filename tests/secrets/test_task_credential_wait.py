from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.credential_type import TaskCredentialWait
from app.models.secret import SecretAuditLog
from app.models.task import AgentScheduledTask
from app.services.secrets import credential_approval as approvals
from app.services.secrets import task_credential_wait as waits

pytestmark = pytest.mark.no_infrastructure
REF_KEY = "_mcp_credential_refs"


def _recovering_task():
    task = SimpleNamespace(
        id=17, user_id=101, agent_id="agent-test", conversation_id="task-test",
        prompt="检查", cron_expr="0 * * * *", status=1, config={}, run_count=0,
    )
    row = SimpleNamespace(
        request_id="request-test", task_id=task.id, user_id=task.user_id,
        run_conversation_id="run-test", trace_id="trace-test", status="resuming",
        task_definition_digest=waits.task_definition_digest(task), resolved_at=None,
        expires_at=datetime.utcnow() + timedelta(minutes=5),
        resume_lease_until=datetime.utcnow() - timedelta(seconds=1),
    )
    return task, row


@pytest.mark.asyncio
@pytest.mark.parametrize("change, claimed, expired, expected", [
    ("definition", False, False, "cancelled"),
    ("owner", False, False, "cancelled"),
    ("deleted", False, False, "cancelled"),
    (None, False, False, "pending"),
    ("definition", True, True, "failed"),
    ("definition", False, True, "expired"),
])
async def test_wait_recovery_rechecks_current_task_without_replaying_claims(change, claimed, expired, expected):
    task, row = _recovering_task()
    stale_task = SimpleNamespace(**vars(task))
    if change == "definition":
        task.prompt = "新的任务指令"
    elif change == "owner":
        task.user_id = 102
    elif change == "deleted":
        task = None
    if expired:
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)

    async def get(model, identifier, **kwargs):
        if model is SecretAuditLog:
            assert identifier == approvals.approval_claim_id(row.request_id)
            return object() if claimed else None
        assert model is AgentScheduledTask and identifier == row.task_id
        # 模拟旧身份映射和可重复读快照；锁定读取并刷新才是当前任务定义。
        fresh_read = kwargs.get("with_for_update") and kwargs.get("populate_existing")
        return task if fresh_read else stale_task

    session = SimpleNamespace(get=AsyncMock(side_effect=get), add=Mock())
    await waits.recover_wait(session, row)
    assert row.status == expected
    assert row.resume_lease_until is None
    assert (row.resolved_at is None) == (expected == "pending")


@pytest.mark.asyncio
async def test_wait_recovery_finds_consumed_claim_outside_stale_transaction_snapshot():
    task, row = _recovering_task()

    async def get(model, identifier, **kwargs):
        if model is SecretAuditLog:
            # claim 已被另一事务提交；普通快照读仍返回不存在。
            fresh_read = kwargs.get("with_for_update") and kwargs.get("populate_existing")
            return object() if fresh_read else None
        assert model is AgentScheduledTask
        return task

    session = SimpleNamespace(get=AsyncMock(side_effect=get), add=Mock())
    await waits.recover_wait(session, row)
    assert row.status == "failed"
    assert row.resolved_at is not None and row.resume_lease_until is None


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [True, False])
async def test_deleting_task_locks_task_before_removing_waits(exists, monkeypatch):
    from app.services import task_center_service as task_center

    task, _ = _recovering_task()
    events = []

    async def get(model, identifier, **kwargs):
        assert model is AgentScheduledTask and identifier == task.id
        assert kwargs.get("with_for_update"), "删除前必须先锁任务行"
        events.append("lock-task")
        return task if exists else None

    async def execute(statement):
        if statement.is_select:
            return SimpleNamespace(scalar_one_or_none=lambda: task if exists else None)
        assert "lock-task" in events, "删除等待记录前必须已持任务锁"
        assert statement.is_delete
        if statement.table.name == TaskCredentialWait.__tablename__:
            events.append("delete-wait")
        else:
            assert statement.table.name == AgentScheduledTask.__tablename__
            events.append("delete-task")

    async def remove_job(task_id):
        assert task_id == task.id
        events.append("remove-job")

    async def commit():
        events.append("commit")

    session = SimpleNamespace(get=get, execute=execute, commit=commit)
    monkeypatch.setattr(task_center.scheduler_service, "remove_task", remove_job)
    monkeypatch.setattr(task_center.scheduler_service, "get_next_run_time", lambda _: None)
    await task_center.TaskCenterService.delete_task(session, task.id)
    assert events == (
        ["lock-task", "remove-job", "delete-wait", "delete-task", "commit"]
        if exists else ["lock-task"]
    )


@pytest.mark.asyncio
async def test_wait_recovery_from_expired_lease_locks_task_before_waits():
    task, row = _recovering_task()
    task.prompt = "租约失效前已修改任务"
    task_locked = False

    async def get(model, identifier, **kwargs):
        nonlocal task_locked
        if model is AgentScheduledTask:
            if kwargs.get("with_for_update"):
                task_locked = True
            return task
        assert model is SecretAuditLog
        return None

    async def execute(statement):
        assert task_locked, "回收等待前必须先锁任务，保持 task → wait 顺序"

    async def scalars(statement):
        assert task_locked
        return SimpleNamespace(all=lambda: [row])

    session = SimpleNamespace(
        get=AsyncMock(side_effect=get), execute=AsyncMock(side_effect=execute),
        scalars=AsyncMock(side_effect=scalars), flush=AsyncMock(), add=Mock(),
    )
    await waits.expire_waits(session, task.id)
    assert row.status == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["cancel", "selection_error"])
async def test_task_resume_recovery_locks_task_before_wait_and_resets_context(monkeypatch, finish):
    from app.services.ai import scheduler_service as scheduler
    from app.services.ai.agent_service import agent_service
    from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
    from app.services.secrets import mcp_profiles

    task, row = _recovering_task()
    row.status = "pending"
    pending = SimpleNamespace(snapshot=SimpleNamespace(
        conversation_id=row.run_conversation_id,
        runner_context={approvals.MCP_CREDENTIAL_REF: {"scheduled_task_id": task.id}},
    ))

    @asynccontextmanager
    async def sessions():
        task_locked = False

        async def get(model, identifier, **kwargs):
            nonlocal task_locked
            if model is AgentScheduledTask:
                if kwargs.get("with_for_update"):
                    task_locked = True
                return task
            if model is TaskCredentialWait:
                assert task_locked, "取消恢复也必须先锁任务，再锁等待记录"
                return row
            assert model is SecretAuditLog
            return None

        yield SimpleNamespace(
            get=AsyncMock(side_effect=get), scalar=AsyncMock(return_value=row),
            execute=AsyncMock(), scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
            commit=AsyncMock(), flush=AsyncMock(), add=Mock(),
        )

    async def interrupted_resume(**kwargs):
        task.prompt = "用户在恢复途中修改了任务"
        if finish == "cancel":
            raise asyncio.CancelledError()
        yield {"type": "error", "code": "CREDENTIAL_SELECTION_INVALID", "content": "合成选择失效"}

    release = AsyncMock()
    monkeypatch.setattr(scheduler, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(scheduler, "_acquire_task_execution_lock", AsyncMock(return_value="task-lock"))
    monkeypatch.setattr(scheduler, "_release_task_execution_lock", release)
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", AsyncMock(return_value=pending))
    monkeypatch.setattr(mcp_profiles, "pending_candidates", AsyncMock(return_value=[object()]))
    monkeypatch.setattr(agent_service, "resume_agentscope_permission_stream", interrupted_resume)
    previous = {"previous-tool": None}
    scope = waits.current_task_credential_refs.set(previous)
    try:
        expected_error = asyncio.CancelledError if finish == "cancel" else mcp_profiles.CredentialInvocationError
        with pytest.raises(expected_error):
            await scheduler.resume_task_credential(task.id, row.request_id, "secret-test", user_info={"user_id": 101})
        assert row.status == "cancelled"
        assert waits.current_task_credential_refs.get() is previous
        release.assert_awaited_once_with(task.id, "task-lock")
    finally:
        waits.current_task_credential_refs.reset(scope)


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    try:
        for table in (AgentScheduledTask.__table__, TaskCredentialWait.__table__, SecretAuditLog.__table__):
            table.create(engine)

        class AsyncFixtureSession:
            def __init__(self, session):
                self.session = session

            def add(self, row):
                self.session.add(row)

            async def get(self, model, identifier, **kwargs):
                return self.session.get(model, identifier, **kwargs)

            async def execute(self, statement):
                return self.session.execute(statement)

            async def scalar(self, statement):
                return self.session.scalar(statement)

            async def scalars(self, statement):
                return self.session.scalars(statement)

            async def refresh(self, row):
                self.session.refresh(row)

            async def flush(self):
                self.session.flush()

            async def commit(self):
                self.session.commit()

            async def rollback(self):
                self.session.rollback()

        @asynccontextmanager
        async def sessions():
            with Session(engine, expire_on_commit=False) as session:
                yield AsyncFixtureSession(session)

        yield sessions
    finally:
        engine.dispose()


def test_fixed_profile_can_enter_native_confirmation():
    ref = {"tool_id": "fixture", "profile_id": "fixture-profile", "revision": 3, "selection_mode": "fixed"}
    tool = SimpleNamespace(spec=SimpleNamespace(metadata={approvals.MCP_CREDENTIAL_REF: ref}))
    assert approvals.confirmation_ref(tool) == ref


@pytest.mark.asyncio
async def test_public_task_config_cannot_create_or_replace_credential_references(session_factory, monkeypatch):
    from app.services import task_center_service as task_center
    from app.schemas.task import TaskResponse

    monkeypatch.setattr(task_center.scheduler_service, "upsert_task", AsyncMock())
    monkeypatch.setattr(task_center.scheduler_service, "get_next_run_time", lambda _: None)
    async with session_factory() as db:
        task = await task_center.TaskCenterService.create_task(
            db, 101, "测试任务", "agent-test", "0 * * * *", "检查",
            config={REF_KEY: {"tool-test": "forged"}, "retry_count": 2},
        )
        assert task.config == {"retry_count": 2}

        task.config = {REF_KEY: {"tool-test": "receipt"}, "retry_count": 2}
        await db.commit()
        updated = await task_center.TaskCenterService.update_task(
            db, task.id, {"config": {REF_KEY: {"tool-test": "replacement"}, "retry_count": 3}},
        )
        assert updated.config == {REF_KEY: {"tool-test": "receipt"}, "retry_count": 3}
        assert TaskResponse.model_validate(updated).config == {"retry_count": 3}


async def _waiting_task(db, *, status="pending"):
    task = AgentScheduledTask(
        name="测试任务", user_id=101, agent_id="agent-test", conversation_id="conversation-test",
        cron_expr="0 * * * *", prompt="检查", config={},
    )
    db.add(task)
    await db.flush()
    wait = waits.add_wait(
        db, task, event={"request_id": "request-test", "expires_at": int(time.time()) + 120},
        trace_id="trace-test", conversation_id="run-test",
    )
    wait.status = status
    ref = {
        "tool_id": "tool-test", "profile_id": "profile-test", "revision": 3,
        "selection_mode": "ask", "scheduled_task_id": task.id,
    }
    pending = SimpleNamespace(
        request_id=wait.request_id, user_id="101",
        snapshot=SimpleNamespace(
            runner_context={approvals.MCP_CREDENTIAL_REF: ref}, conversation_id=wait.run_conversation_id,
            tool_call={"input": "{}"}, created_at=time.time(),
        ),
    )
    await db.commit()
    return task, wait, ref, pending


@pytest.mark.asyncio
async def test_cancelled_task_cannot_offer_to_create_a_credential(session_factory, monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints import chat
    from app.core import orm
    from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
    from app.services.secrets import mcp_profiles

    async with session_factory() as db:
        _, wait, _, pending = await _waiting_task(db, status="cancelled")
    monkeypatch.setattr(orm, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(pending_agentscope_confirmations, "peek_async", AsyncMock(return_value=pending))
    monkeypatch.setattr(mcp_profiles, "pending_target", AsyncMock(side_effect=AssertionError("已取消的任务不能查询凭据")))
    with pytest.raises(HTTPException) as caught:
        await chat.get_tool_credential_options(wait.request_id, {"user_id": 101})
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "TASK_CREDENTIAL_WAIT_CLOSED"


@pytest.mark.asyncio
async def test_remembered_selection_is_scoped_and_uses_a_durable_claim(session_factory):
    from app.services.secrets.mcp_profiles import CredentialInvocationError

    async with session_factory() as db:
        task, _, ref, pending = await _waiting_task(db, status="resuming")
        claim = approvals.approval_for_pending(pending, "secret-test", remember_credential=True)
        task_id = task.id
        active_refs = {"tool-test": waits.TaskCredentialRef(task_id, "101", "profile-test", 3, "old-secret")}
        scope = waits.current_task_credential_refs.set(active_refs)
        try:
            assert await approvals.claim_approval(db, claim)
            assert waits.task_credential_secret_id(ref, "101") == "secret-test"
            assert not await approvals.claim_approval(
                db, approvals.approval_for_pending(pending, "different-secret", remember_credential=True),
            )
            assert waits.task_credential_secret_id(ref, "101") == "secret-test"
        finally:
            waits.current_task_credential_refs.reset(scope)

    async with session_factory() as db:
        task = await db.get(AgentScheduledTask, task_id)
        assert task.config == {REF_KEY: {"tool-test": approvals.approval_claim_id(pending.request_id)}}
        refs = await waits.load_task_credential_refs(db, task)
        scope = waits.current_task_credential_refs.set(refs)
        try:
            assert waits.task_credential_secret_id(ref, "101") == "secret-test"
            assert waits.task_credential_secret_id(ref, "102") is None
            assert waits.task_credential_secret_id({**ref, "tool_id": "other-tool"}, "101") is None
            with pytest.raises(CredentialInvocationError) as caught:
                waits.task_credential_secret_id({**ref, "revision": 4}, "101")
            assert caught.value.code == "CREDENTIAL_PROFILE_CHANGED"
        finally:
            waits.current_task_credential_refs.reset(scope)
        assert waits.task_credential_secret_id(ref, "101") is None

        other_task = AgentScheduledTask(
            name="其他任务", user_id=101, agent_id="agent-test", conversation_id="other-conversation",
            cron_expr="0 * * * *", prompt="检查", config=task.config,
        )
        db.add(other_task)
        await db.flush()
        scope = waits.current_task_credential_refs.set(await waits.load_task_credential_refs(db, other_task))
        try:
            with pytest.raises(CredentialInvocationError) as wrong_task:
                waits.task_credential_secret_id(ref, "101")
            assert wrong_task.value.code == "CREDENTIAL_PROFILE_CHANGED"
        finally:
            waits.current_task_credential_refs.reset(scope)



@pytest.mark.asyncio
async def test_native_confirmation_distinguishes_interleaved_calls(monkeypatch):
    from app.services.ai.runtime.agentscope import event_stream, tools as runtime_tools
    from app.services.ai.runtime.agentscope.confirmations import pending_agentscope_confirmations
    from app.services.ai.runtime.agentscope.middleware import ToolPermissionMiddleware
    from app.services.secrets import mcp_profiles

    async def select_reason(_ref, arguments, _actor_id):
        return arguments.get("test_reason")

    monkeypatch.setattr(mcp_profiles, "credential_selection_reason", select_reason)
    monkeypatch.setattr(runtime_tools, "_enforce_tool_forbidden", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime_tools, "_enforce_command_blacklist", AsyncMock(return_value=None))
    ref = {"tool_id": "tool-test", "profile_id": "profile-test", "revision": 3, "selection_mode": "fixed"}
    spec = runtime_tools.RuntimeToolSpec(
        name="fixture-tool", description="fixture", parameters_schema={}, source_type="mcp",
        callable=AsyncMock(), permission_scope="ask", metadata={approvals.MCP_CREDENTIAL_REF: ref},
    )
    tool = runtime_tools.AgentScopeRuntimeTool(spec, approval_mode="ask", user_id=101)
    middleware = ToolPermissionMiddleware()

    async def check(**kwargs):
        return await tool.check_permissions(kwargs["tool_input"], None)

    calls = {
        key: SimpleNamespace(id=key, name=tool.name, input="{}")
        for key in ("first", "second", "ordinary")
    }
    expected = {
        "first": "credential_missing",
        "second": "credential_ambiguous",
        "ordinary": None,
    }
    for call_id, reason in expected.items():
        await middleware.on_check_permission(None, {
            "tool_call": calls[call_id], "tool": tool, "tool_input": {"test_reason": reason},
        }, check)

    registered = {}

    async def register(**kwargs):
        call_id = kwargs["tool_call"].id
        registered[call_id] = kwargs["runner_context"].get(approvals.MCP_CREDENTIAL_REF)
        return SimpleNamespace(request_id=call_id, reply_id="reply-test", snapshot=SimpleNamespace(created_at=time.time()))

    monkeypatch.setattr(pending_agentscope_confirmations, "register", register)
    runner = SimpleNamespace(
        trace_id="trace-test", conversation_id="run-test",
        _runtime_user_id=lambda: "101", _runtime_agent_name=lambda: "fixture",
        _runner_context=lambda **_: {},
    )
    for call_id in ("second", "ordinary", "first"):
        chunks = [chunk async for chunk in event_stream.stream_pending_tool_interrupt(
            event=SimpleNamespace(tool_calls=[calls[call_id]], reply_id="reply-test"),
            agent=None, runner=runner, tools=[tool], native_model=None, state={},
            kind="permission", sse_type="permission_required",
        )]
        reason = expected[call_id]
        assert chunks[0]["credential_selection_required"] is bool(reason)
        assert registered[call_id] == ({**ref, "selection_reason": reason} if reason else None)
    assert not tool.credential_selection_reasons


@pytest.mark.asyncio
async def test_credential_creation_rechecks_live_candidates(monkeypatch):
    from fastapi import HTTPException

    from app.api.v1.endpoints import chat
    from app.core import orm
    from app.services.secrets import center, mcp_profiles, type_catalog

    ref = {"selection_reason": "credential_missing"}
    pending = SimpleNamespace()
    profile = SimpleNamespace(type_key="imap_password", type_version=1, selection_mode="infer")
    server = SimpleNamespace(sse_url="https://mcp.example.test")
    target = AsyncMock(return_value=(profile, server, SimpleNamespace(dept_code=None)))
    create = AsyncMock()
    visible = AsyncMock(return_value=False)
    monkeypatch.setattr(chat, "_pending_credential_request", AsyncMock(return_value=(pending, ref, "101")))
    monkeypatch.setattr(mcp_profiles, "pending_target", target)
    monkeypatch.setattr(mcp_profiles, "pending_account_hint", lambda *_: "fixture@example.test")
    monkeypatch.setattr(mcp_profiles, "list_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(mcp_profiles, "has_visible_candidate", visible)
    monkeypatch.setattr(type_catalog, "get_type", AsyncMock(return_value=SimpleNamespace(enabled=True, name="邮件")))
    monkeypatch.setattr(type_catalog, "get_version", AsyncMock(return_value=SimpleNamespace(schema_json={})))
    monkeypatch.setattr(center, "create_secret", create)

    @asynccontextmanager
    async def db():
        yield object()

    monkeypatch.setattr(orm, "AsyncSessionLocal", db)
    actor = {"user_id": 101}
    result = await chat.get_tool_credential_options("request-test", actor)
    assert result["creation"]["account_hint"] == "fixture@example.test"
    visible.return_value = True
    result = await chat.get_tool_credential_options("request-test", actor)
    assert result["creation"] is None
    assert result["selection_reason"] == "credential_selection_invalid"

    with pytest.raises(HTTPException) as caught:
        await chat.create_tool_credential(
            "request-test", chat.ToolCredentialCreateRequest(account="fixture@example.test", fields={}), actor,
        )
    assert caught.value.detail["code"] == "CREDENTIAL_SELECTION_INVALID"
    create.assert_not_awaited()
