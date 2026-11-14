"""共享结构化凭据沿用凭据中心授权；使用系统数据库隔离 fixture 和合成值。"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import json
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.api.portal.endpoints import mcp_credential_profiles as routes
from app.models.credential_type import McpCredentialUseGrant, StructuredCredential
from app.models.secret import SecretGrant
from app.models.user import User
from app.services.ai.tools import mcp_credential_candidates as discovery
from app.services.secrets import mcp_profiles
from app.services.secrets.credential_approval import MCP_CREDENTIAL_REF
from app.services.secrets.task_credential_wait import TaskCredentialRef, current_task_credential_refs
from tests.secrets.test_structured_secret_lifecycle import credential_db, create_imap, mcp_fixture  # noqa: F401
from tests.temporary_database import init_infrastructure  # noqa: F401


pytestmark = pytest.mark.asyncio
ACCOUNT = "mcp.mail.user-secret"


async def shared_credential(db, grantee_type="user"):
    row = await create_imap(db, scope="system", owner="", account=ACCOUNT, key="shared.mail")
    row.name = "adb的邮箱凭证"
    await db.execute(update(User).where(User.id == 101).values(dept_code="dept-a"))
    grant = SecretGrant(id="shared", secret_id=row.id, grantee_type=grantee_type,
                        grantee_id={"user": "101", "dept": "dept-a", "all": ""}[grantee_type])
    db.add(grant)
    await db.commit()
    return row, grant


async def invoke(db, server, tool, profile, arguments, selected_id=None):
    return await mcp_profiles.prepare_mcp_arguments(
        db, server, "list_mail", arguments,
        mcp_profiles.McpCredentialInvocation(tool.id, profile.revision, profile_id=profile.profile_id,
                                             selected_secret_id=selected_id),
        user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
    )


@pytest.mark.parametrize("grantee_type", ["user", "dept", "all"])
async def test_shared_discovery_keyword_and_execution_use_center_grant(mcp_fixture, monkeypatch, grantee_type):
    db, server, tool, _, profile = mcp_fixture
    if grantee_type == "all":
        # 截图场景：本人管理的私有 MCP 同样可使用已共享给自己的凭据。
        server.scope, server.user_id = "personal", 101
    shared, grant = await shared_credential(db, grantee_type)
    ref = {"tool_id": tool.id, "revision": profile.revision, "profile_id": profile.profile_id,
           "selection_mode": "infer"}
    monkeypatch.setattr(discovery, "mounted_mcp_tool_capability", lambda _: ("101", {MCP_CREDENTIAL_REF: ref}))
    monkeypatch.setattr(discovery, "_catalog_enabled", AsyncMock(return_value=True))
    result = json.loads(await discovery.list_mcp_credential_candidates.ainvoke({"tool_name": "fixture", "keyword": "adb"}))
    assert result["items"] == [{"name": "adb的邮箱凭证", "account": ACCOUNT, "owner_scope": "system"}]
    assert "synthetic-mail-only" not in repr(result)
    # 名称片段用于搜索，credential_account 仍是唯一账号的精确匹配。
    assert json.loads(await discovery.list_mcp_credential_candidates.ainvoke({"tool_name": "fixture", "account": "adb"}))["items"] == []
    options = await routes.candidates(tool.id, user={"user_id": 101, "dept_code": "dept-a"}, db=db)
    assert shared.id in {item["id"] for item in options["data"]["items"]}
    arguments = {"credential_account": result["items"][0]["account"], "limit": 2}
    outgoing = await invoke(db, server, tool, profile, arguments)
    assert outgoing.arguments == {"username": "fixture-user", "password": "synthetic-mail-only",
                                  "host": "imap.example.test", "port": 993, "limit": 2}
    assert arguments == {"credential_account": ACCOUNT, "limit": 2}
    if grantee_type == "all":
        with pytest.raises(mcp_profiles.CredentialInvocationError) as private_service:
            await mcp_profiles.pending_candidates(db, ref, "102")
        assert private_service.value.code == "CREDENTIAL_ACCESS_DENIED"
    # 撤销凭据中心授权后，候选和下一次实际执行均立即拒绝。
    await db.delete(grant)
    await db.commit()
    assert json.loads(await discovery.list_mcp_credential_candidates.ainvoke({"tool_name": "fixture", "keyword": "adb"}))["items"] == []
    with pytest.raises(mcp_profiles.CredentialInvocationError) as denied:
        await invoke(db, server, tool, profile, arguments)
    assert denied.value.code == "CREDENTIAL_MISSING"


@pytest.mark.parametrize("mode", ["fixed", "ask", "task"])
async def test_shared_fixed_interactive_and_task_choices_revalidate_sharing(mcp_fixture, mode):
    db, server, tool, _, profile = mcp_fixture
    shared, grant = await shared_credential(db)
    if mode != "task":
        profile.selection_mode = mode
        await db.commit()
    if mode == "fixed":
        await routes.set_fixed(tool.id, routes.FixedRequest(secret_id=shared.id,
            expected_revision=profile.revision, expected_profile_id=profile.profile_id),
            user={"user_id": 101}, db=db)
    ref = {"tool_id": tool.id, "revision": profile.revision, "profile_id": profile.profile_id}
    assert [row[0].id for row in await mcp_profiles.pending_candidates(db, ref, "101", secret_id=shared.id)] == [shared.id]
    token = current_task_credential_refs.set({tool.id: TaskCredentialRef(
        17, "101", profile.profile_id, profile.revision, shared.id,
    )} if mode == "task" else None)
    try:
        selected = shared.id if mode == "ask" else None
        assert (await invoke(db, server, tool, profile, {}, selected)).arguments["username"] == "fixture-user"
        await db.delete(grant)
        await db.commit()
        assert await mcp_profiles.pending_candidates(db, ref, "101", secret_id=shared.id) == []
        with pytest.raises(mcp_profiles.CredentialInvocationError) as denied:
            await invoke(db, server, tool, profile, {}, selected)
        assert denied.value.code == "CREDENTIAL_SELECTION_INVALID"
    finally:
        current_task_credential_refs.reset(token)


@pytest.mark.parametrize("blocked_by", ["no_share", "expired_share", "other_user", "other_department",
                                        "disabled", "expired", "host", "scene", "version"])
async def test_shared_candidates_retain_scope_and_use_constraints(mcp_fixture, blocked_by):
    db, server, tool, _, profile = mcp_fixture
    shared, grant = await shared_credential(db)
    if blocked_by == "no_share":
        await db.delete(grant)
    elif blocked_by == "expired_share":
        grant.expires_at = datetime.utcnow() - timedelta(seconds=1)
    elif blocked_by == "other_user":
        grant.grantee_id = "102"
    elif blocked_by == "other_department":
        grant.grantee_type, grant.grantee_id = "dept", "dept-b"
    elif blocked_by == "disabled":
        shared.status = "disabled"
    elif blocked_by == "expired":
        shared.expires_at = datetime.utcnow() - timedelta(seconds=1)
    elif blocked_by == "host":
        shared.allowed_hosts = ["other.example.test"]
    elif blocked_by == "scene":
        shared.allowed_scenarios = ["api_tool"]
    else:
        payload = await db.get(StructuredCredential, shared.id)
        payload.type_version = 2
    # 历史逐工具授权不能绕过凭据中心授权或其它使用约束。
    db.add(McpCredentialUseGrant(tool_id=tool.id, secret_id=shared.id, grantee_type="all", grantee_id=""))
    await db.commit()
    assert await mcp_profiles.list_candidates(db, profile, server, user_id="101", dept_code="dept-a", secret_id=shared.id) == []
    with pytest.raises(mcp_profiles.CredentialInvocationError):
        await invoke(db, server, tool, profile, {"credential_account": ACCOUNT})


async def test_legacy_tool_grant_routes_are_explicitly_inactive_and_never_change_sharing(mcp_fixture):
    db, _, tool, _, profile = mcp_fixture
    shared, _ = await shared_credential(db)
    admin = {"user_id": 999, "role": "admin"}
    db.add(McpCredentialUseGrant(tool_id=tool.id, secret_id=shared.id, grantee_type="all", grantee_id=""))
    # 历史记录无需重新确认当前映射，也不能借旧 API 建立新的全局共享权限。
    profile.schema_digest = "outdated"
    await db.commit()
    history = await routes.list_grants(tool.id, admin=admin, db=db)
    assert history["deprecated"] and history["effective"] is False and "凭据中心" in history["message"]
    assert len(history["data"]) == 1
    with pytest.raises(HTTPException) as stopped:
        await routes.put_grant(tool.id, routes.GrantRequest(secret_id=shared.id, grantee_type="all",
            expected_revision=profile.revision, expected_profile_id=profile.profile_id), admin=admin, db=db)
    assert stopped.value.status_code == 410 and "凭据中心" in stopped.value.detail["message"]
    removed = await routes.delete_grant(tool.id, shared.id, "all", admin=admin, db=db)
    assert removed["effective"] is False and "凭据中心" in removed["message"]
    assert await db.scalar(select(func.count()).select_from(McpCredentialUseGrant)) == 0
    assert await db.scalar(select(func.count()).select_from(SecretGrant)) == 1


async def test_shared_and_personal_same_account_requires_explicit_choice(mcp_fixture):
    db, server, tool, _, profile = mcp_fixture
    shared, _ = await shared_credential(db)
    await create_imap(db, owner="101", account=ACCOUNT, key="personal.same-account")
    with pytest.raises(mcp_profiles.CredentialInvocationError) as ambiguous:
        await invoke(db, server, tool, profile, {"credential_account": ACCOUNT})
    assert ambiguous.value.code == "CREDENTIAL_AMBIGUOUS"
    assert (await invoke(db, server, tool, profile, {}, selected_id=shared.id)).arguments["host"] == "imap.example.test"


async def test_sharing_does_not_make_another_users_private_mcp_accessible(mcp_fixture):
    db, server, tool, _, profile = mcp_fixture
    await shared_credential(db, "all")
    server.scope, server.user_id = "personal", 102
    await db.commit()
    ref = {"tool_id": tool.id, "revision": profile.revision, "profile_id": profile.profile_id}
    with pytest.raises(mcp_profiles.CredentialInvocationError) as denied:
        await mcp_profiles.pending_candidates(db, ref, "101")
    assert denied.value.code == "CREDENTIAL_ACCESS_DENIED"
    with pytest.raises(mcp_profiles.CredentialInvocationError) as denied:
        await invoke(db, server, tool, profile, {"credential_account": ACCOUNT})
    assert denied.value.code == "CREDENTIAL_ACCESS_DENIED"
