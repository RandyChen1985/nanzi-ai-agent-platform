"""固定凭据草稿候选与映射/绑定原子保存；只使用系统数据库隔离 fixture。"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.portal.endpoints import mcp_credential_profiles as routes
from app.models.credential_type import McpCredentialProfile
from app.models.secret import SecretAuditLog, SecretBinding, SecretGrant
from app.services.secrets import mcp_profiles
from tests.secrets.test_structured_secret_lifecycle import credential_db, create_imap, mcp_fixture  # noqa: F401
from tests.temporary_database import init_infrastructure  # noqa: F401


pytestmark = pytest.mark.asyncio
ADMIN = {"user_id": 101, "role": "admin"}


def request(profile, **changes):
    data = {
        "type_key": profile.type_key, "type_version": profile.type_version,
        "selection_mode": "fixed", "mappings": profile.mappings,
        "expected_revision": profile.revision, "expected_profile_id": profile.profile_id,
        "model_result_fields": ["$"],
    }
    data.update(changes)
    return routes.ProfileRequest(**data)


async def binding(db, tool_id, user_id="101"):
    return await db.scalar(select(SecretBinding).where(
        SecretBinding.slot == f"group:{tool_id}", SecretBinding.bound_by_user_id == user_id,
    ))


@pytest.mark.parametrize("profile_state", ["infer", "missing", "stale"])
async def test_draft_candidates_work_before_saving_fixed_profile(mcp_fixture, profile_state):
    db, server, tool, personal, profile = mcp_fixture
    tool_id, personal_id = tool.id, personal.id
    server.scope, server.user_id = "personal", 101
    if profile_state == "missing":
        await db.delete(profile)
    elif profile_state == "stale":
        profile.schema_digest = "outdated"
    await db.commit()
    result = await routes.candidates(tool_id, type_key="imap_password", type_version=1, user={"user_id": 101}, db=db)
    assert [item["id"] for item in result["data"]["items"]] == [personal_id]
    assert result["data"]["fixed_secret_id"] is None
    assert "synthetic-mail-only" not in repr(result)
    assert "fixture-user" not in repr(result)
    persisted = await db.get(McpCredentialProfile, tool_id)
    assert (persisted is None) if profile_state == "missing" else persisted.selection_mode == "infer"
    if profile_state == "infer":
        await routes.candidates(tool_id, user={"user_id": 101}, db=db)
    else:
        with pytest.raises(HTTPException):
            await routes.candidates(tool_id, user={"user_id": 101}, db=db)


@pytest.mark.parametrize("draft", [
    {"type_key": "imap_password"}, {"type_version": 1},
    {"type_key": "scalar", "type_version": 1}, {"type_key": "missing", "type_version": 1},
])
async def test_draft_candidates_validate_both_structured_type_and_version(mcp_fixture, draft):
    db, _, tool, _, _ = mcp_fixture
    with pytest.raises(HTTPException) as invalid:
        await routes.candidates(tool.id, **draft, user=ADMIN, db=db)
    assert invalid.value.status_code in (400, 404, 422)


@pytest.mark.parametrize("user", [{"user_id": 101}, {"role": "admin"}])
async def test_draft_candidates_require_control_access_and_identity(mcp_fixture, user):
    db, _, tool, _, _ = mcp_fixture
    with pytest.raises(HTTPException) as denied:
        await routes.candidates(tool.id, type_key="imap_password", type_version=1, user=user, db=db)
    assert denied.value.status_code == 403


async def test_draft_candidates_use_current_type_and_shared_permissions(mcp_fixture):
    db, server, tool, personal, profile = mcp_fixture
    shared = await create_imap(db, scope="system", owner="", account="shared", key="shared")
    hidden = await create_imap(db, scope="system", owner="", account="hidden", key="hidden")
    blocked = await create_imap(db, owner="101", account="blocked", key="blocked")
    blocked.allowed_hosts = ["elsewhere.test"]
    db.add(SecretGrant(id="shared", secret_id=shared.id, grantee_type="all", grantee_id=""))
    await db.commit()
    result = await routes.candidates(tool.id, type_key="imap_password", type_version=1, user=ADMIN, db=db)
    assert {item["id"] for item in result["data"]["items"]} == {personal.id, shared.id}
    assert hidden.id not in repr(result)
    empty = await routes.candidates(tool.id, type_key="database", type_version=1, user=ADMIN, db=db)
    assert empty["data"]["items"] == []


async def test_save_fixed_profile_and_personal_binding_together_then_inject(mcp_fixture):
    db, server, tool, personal, profile = mcp_fixture
    result = await routes.put_profile(tool.id, request(profile, fixed_secret_id=personal.id), user=ADMIN, db=db)
    assert result["data"]["selection_mode"] == "fixed"
    assert (await binding(db, tool.id)).secret_id == personal.id
    options = await routes.candidates(tool.id, type_key="imap_password", type_version=1, user=ADMIN, db=db)
    assert options["data"]["fixed_secret_id"] == personal.id
    other_type = await routes.candidates(tool.id, type_key="database", type_version=1, user=ADMIN, db=db)
    assert other_type["data"]["fixed_secret_id"] is None
    outgoing = await mcp_profiles.prepare_mcp_arguments(
        db, server, "list_mail", {"limit": 2},
        mcp_profiles.McpCredentialInvocation(tool.id, profile.revision, profile_id=profile.profile_id),
        user_info={"user_id": "101"}, trace_id=None, conversation_id=None,
    )
    assert outgoing.arguments["password"] == "synthetic-mail-only"
    assert "credential_account" not in outgoing.arguments


async def test_unavailable_fixed_binding_is_not_silently_treated_as_unbound(mcp_fixture):
    db, _, tool, personal, profile = mcp_fixture
    await routes.put_profile(tool.id, request(profile, fixed_secret_id=personal.id), user=ADMIN, db=db)
    personal.status = "disabled"
    await db.commit()
    result = await routes.candidates(tool.id, type_key="imap_password", type_version=1, user=ADMIN, db=db)
    assert result["data"]["fixed_secret_id"] == personal.id
    assert result["data"]["items"] == []


@pytest.mark.parametrize("usable", [True, False])
async def test_new_profile_and_fixed_binding_are_created_atomically(mcp_fixture, usable):
    db, _, tool, personal, profile = mcp_fixture
    tool_id, secret_id = tool.id, personal.id
    payload = request(profile, expected_revision=0, expected_profile_id=None,
                      fixed_secret_id=secret_id if usable else "unavailable")
    await db.delete(profile)
    await db.commit()
    if usable:
        result = await routes.put_profile(tool_id, payload, user=ADMIN, db=db)
        assert result["data"]["revision"] == 1
        assert (await binding(db, tool_id)).secret_id == secret_id
    else:
        with pytest.raises(HTTPException):
            await routes.put_profile(tool_id, payload, user=ADMIN, db=db)
        assert await db.get(McpCredentialProfile, tool_id) is None
        assert await binding(db, tool_id) is None


async def test_fixed_save_failure_rolls_back_mapping_binding_and_audit(mcp_fixture):
    db, _, tool, personal, profile = mcp_fixture
    await routes.put_profile(tool.id, request(profile, fixed_secret_id=personal.id), user=ADMIN, db=db)
    tool_id, revision, secret_id = tool.id, profile.revision, personal.id
    audit_count = await db.scalar(select(func.count()).select_from(SecretAuditLog))
    payload = request(profile, fixed_secret_id="unavailable", mappings={**profile.mappings, "limit": "port"})
    with pytest.raises(HTTPException) as unavailable:
        await routes.put_profile(tool_id, payload, user=ADMIN, db=db)
    assert unavailable.value.status_code == 404
    # 用同一会话重新查询持久化状态，不能只断言未过期的 ORM 对象。
    db.expire_all()
    persisted = await db.get(McpCredentialProfile, tool_id)
    assert persisted.revision == revision and "limit" not in persisted.mappings
    assert (await binding(db, tool_id)).secret_id == secret_id
    assert await db.scalar(select(func.count()).select_from(SecretAuditLog)) == audit_count


async def test_fixed_save_optimistic_conflict_does_not_rebind(mcp_fixture):
    db, _, tool, personal, profile = mcp_fixture
    stale = request(profile, fixed_secret_id=None)
    await routes.put_profile(tool.id, request(profile, fixed_secret_id=personal.id), user=ADMIN, db=db)
    tool_id, secret_id = tool.id, personal.id
    with pytest.raises(HTTPException) as conflict:
        await routes.put_profile(tool_id, stale, user=ADMIN, db=db)
    assert conflict.value.detail["code"] == "CREDENTIAL_REVISION_CONFLICT"
    assert (await binding(db, tool_id)).secret_id == secret_id


async def test_omitted_binding_is_compatible_and_explicit_null_unbinds_only_self(mcp_fixture):
    db, _, tool, personal, profile = mcp_fixture
    await routes.put_profile(tool.id, request(profile, fixed_secret_id=personal.id), user=ADMIN, db=db)
    db.add(SecretBinding(id="other-binding", secret_id=personal.id, consumer_type="mcp", consumer_id=tool.server_id,
                         slot=f"group:{tool.id}", bound_by_user_id="102", strategy="fixed"))
    await db.commit()
    await routes.put_profile(tool.id, request(profile), user=ADMIN, db=db)
    assert (await binding(db, tool.id)).secret_id == personal.id
    await routes.put_profile(tool.id, request(profile, fixed_secret_id=None), user=ADMIN, db=db)
    assert await binding(db, tool.id) is None
    assert (await binding(db, tool.id, "102")).secret_id == personal.id


@pytest.mark.parametrize("mode", ["infer", "ask"])
async def test_nonfixed_profile_rejects_explicit_fixed_field(mcp_fixture, mode):
    db, _, tool, _, profile = mcp_fixture
    with pytest.raises(HTTPException) as invalid:
        await routes.put_profile(tool.id, request(profile, selection_mode=mode, fixed_secret_id=None), user=ADMIN, db=db)
    assert invalid.value.status_code == 422
