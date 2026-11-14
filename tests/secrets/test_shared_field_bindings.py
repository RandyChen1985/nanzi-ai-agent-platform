"""系统共享字段绑定与实际解析使用同一授权；仅使用平台隔离数据库和合成值。"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.models.mcp import McpServer
from app.models.script_tool import SysScriptTool
from app.models.secret import SecretAuditLog, SecretBinding, SecretUserPref
from app.models.tool import SysApiTool
from app.services.secrets import center, field_binding, resolver, structured_payload
from app.services.secrets.policy import DENY_BINDING, DENY_GRANT
from app.services.secrets.resolver import SecretResolver
from tests.secrets.test_structured_secret_lifecycle import credential_db  # noqa: F401
from tests.temporary_database import init_infrastructure  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,scene,slot,field,expected", [
    ("api_tool", "api_tool", "header:Authorization", "password", "synthetic-shared-password"),
    ("script_tool", "script_env", "env:MAIL_PORT", "port", 993),
    ("mcp_server", "mcp", "header:Authorization", "token", "synthetic-shared-token"),
])
async def test_shared_fields_can_be_bound_and_used_only_while_authorized(
    credential_db, monkeypatch, kind, scene, slot, field, expected,
):
    db = credential_db
    monkeypatch.setattr(SecretResolver, "_platform_flags", AsyncMock(return_value=(True, True, 3)))
    if kind == "mcp_server":
        consumer = McpServer(id="consumer", server_name="fixture", sse_url="https://target.example.test/rpc",
                             scope="personal", user_id=101)
        data = structured_payload.StructuredInput("mcp_token", 1, "shared-account", {"token": expected})
    else:
        if kind == "script_tool":
            consumer = SysScriptTool(id="consumer", name="fixture_shared_script", display_name="fixture", code="pass",
                                     parameter_schema="{}", requirements="[]", is_active=True, source="custom")
        else:
            consumer = SysApiTool(id="consumer", name="fixture_shared_api", url_template="https://target.example.test/rpc",
                                  method="POST", headers='{"Authorization":""}', is_active=True)
        data = structured_payload.StructuredInput("imap_password", 1, "shared-account", {
            "username": "synthetic-shared-user", "host": "mail.example.test", "password": "synthetic-shared-password",
        })
    db.add_all([consumer, SecretUserPref(user_id="101", allow_script_env=True)])
    await db.commit()
    row = await center.create_secret(db, owner_scope="system", owner_id="", secret_key="shared.fields", name="合成共享凭据",
                                    allowed_scenarios=[scene], allowed_hosts=["target.example.test"], structured=data)
    options = dict(secret_id=row.id, consumer_type=kind, consumer_id=consumer.id,
                   slot=slot, value_field=field, bound_by_user_id="101", actor_id="101", dept_code="dept-a")
    with pytest.raises(center.SecretPermissionError):
        await center.bind_secret(db, **options)
    assert (await db.scalars(select(SecretBinding))).all() == []

    grant = await center.grant_secret(db, secret_id=row.id, grantee_type="dept", grantee_id="dept-a", granted_by="999")
    binding = await center.bind_secret(db, **options)
    assert binding.bound_by_user_id == "101"
    with pytest.raises(ValueError):
        await field_binding.resolve_field(db, row, binding, user_id="102", expected_target=binding.target_digest)
    params = dict(user_id="101", dept_code="dept-a", scene=scene, consumer_type=kind,
                  consumer_id=consumer.id, slot=slot, target_host="target.example.test",
                  target_binding_digest=binding.target_digest)
    result = await resolver.secret_resolver.resolve(**params)
    assert result.ok and result.value.reveal() == expected
    assert result.secret_id == row.id

    # 共享授权不改变个人绑定归属，也不绕过执行时授权、场景和目标检查。
    result = await resolver.secret_resolver.resolve(**{**params, "user_id": "102"})
    assert not result.ok and result.denial == DENY_BINDING
    for changes in ({"dept_code": "dept-b"}, {"target_binding_digest": "changed"}, {"scene": "agent"}):
        result = await resolver.secret_resolver.resolve(**{**params, **changes})
        assert not result.ok and result.value is None
    grant.expires_at = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()
    result = await resolver.secret_resolver.resolve(**params)
    assert not result.ok and result.denial == DENY_GRANT
    grant.expires_at = None
    await db.commit()
    await center.revoke_grant(db, secret_id=row.id, grantee_type="dept", grantee_id="dept-a", actor_id="999")
    result = await resolver.secret_resolver.resolve(**params)
    assert not result.ok and result.denial == DENY_GRANT
    assert (await db.scalars(select(SecretBinding))).all()  # 保留绑定，调用时重新校验。
    audits = json.dumps((await db.scalars(select(SecretAuditLog.detail))).all())
    assert "synthetic-shared-password" not in audits and "synthetic-shared-token" not in audits
