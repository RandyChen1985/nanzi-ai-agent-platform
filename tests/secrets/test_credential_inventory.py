"""对话凭据目录的可见库存与旧编号兼容回归。"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core import orm
from app.core.context import AgentContext
from app.models.credential_type import StructuredCredential
from app.models.permission import Role, UserRoleRelation
from app.models.secret import SecretGrant, UserSecret
from app.models.user import User
from app.services.ai.grounding.ledger import classify_evidence_result
from app.services.ai.grounding.models import EvidenceStatus
from app.services.ai.runtime.agentscope.tool_result import build_tool_result_envelope
from app.services.ai.tools import credential_catalog_tools as catalog
from app.services.config_service import ConfigService
from app.services.secrets.injection import _resolve_choices

pytestmark = pytest.mark.no_infrastructure

USER_ID = 101
DEPARTMENT = "dept-fixture"
PAST = datetime.utcnow() - timedelta(days=1)
PRIVATE_NAME = "synthetic display name"
PRIVATE_VALUE = "synthetic credential value"
PRIVATE_ACCOUNT = "synthetic-address@example.test"


@pytest.fixture
def inventory(monkeypatch):
    engine = create_engine("sqlite://")
    for model in (User, Role, UserRoleRelation, UserSecret, SecretGrant, StructuredCredential):
        model.__table__.create(engine)

    class AsyncInventorySession:
        def __init__(self, session):
            self.session = session

        async def execute(self, statement, *args, **kwargs):
            return self.session.execute(statement, *args, **kwargs)

        async def get(self, model, identifier):
            return self.session.get(model, identifier)

    @asynccontextmanager
    async def sessions():
        with Session(engine) as session:
            yield AsyncInventorySession(session)

    async def platform_settings():
        return {"secret_policy_allow_catalog": {"value": "true"}}

    monkeypatch.setattr(catalog, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(orm, "AsyncSessionLocal", sessions)
    context = AgentContext(agent_id="inventory-fixture", agent_name="测试智能体", user_id=USER_ID)
    monkeypatch.setattr(catalog, "get_current_agent_context", lambda: context)
    monkeypatch.setattr(ConfigService, "get_all_from_db", staticmethod(platform_settings))
    try:
        with Session(engine) as session:
            session.add(User(id=USER_ID, user_name="inventory-member", dept_code=DEPARTMENT))
            session.commit()
            yield session
    finally:
        engine.dispose()


def _secret(identifier, key, *, owner_id=str(USER_ID), **options):
    return UserSecret(
        id=identifier,
        secret_key=key,
        name=PRIVATE_NAME,
        value_encrypted=PRIVATE_VALUE,
        owner_id=owner_id,
        **options,
    )


def _grant(secret_id, kind, grantee, *, expires_at=None):
    return SecretGrant(
        id=f"{secret_id}-{kind}",
        secret_id=secret_id,
        grantee_type=kind,
        grantee_id=str(grantee),
        expires_at=expires_at,
    )


async def _catalog(**arguments):
    return json.loads(await catalog.list_my_credentials.ainvoke(arguments))


def _assert_no_instance_details(output, *private_values):
    serialized = json.dumps(output, ensure_ascii=False)
    for private in (PRIVATE_NAME, PRIVATE_VALUE, PRIVATE_ACCOUNT, *private_values):
        assert private not in serialized
    for key in ("name", "account", "secret_key", "secret_id", "owner_id", "value_encrypted", "payload_encrypted"):
        assert json.dumps(key) not in serialized


async def test_structured_credentials_count_without_a_legacy_choice(inventory):
    inventory.add_all([
        _secret("structured-id", "alice-mail", credential_format="structured_v1"),
        StructuredCredential(
            secret_id="structured-id",
            owner_scope="user",
            owner_id=str(USER_ID),
            type_key="imap_password",
            type_version=1,
            account=PRIVATE_ACCOUNT,
            account_digest="synthetic-digest",
            key_id="synthetic-key-id",
            payload_encrypted=PRIVATE_VALUE,
            public_projection={},
            secret_fields_set=["password"],
            extra_schema={},
        ),
    ])
    inventory.commit()

    output = await _catalog()
    assert {key: output.get(key) for key in ("status", "total", "enabled", "legacy_choices", "truncated")} == {
        "status": "success",
        "total": 1,
        "enabled": 1,
        "legacy_choices": {"total": 0, "items": [], "truncated": False},
        "truncated": False,
    }
    assert output["owned"] == {
        "total": 1,
        "enabled": 1,
        "single_value": {"total": 0, "enabled": 0},
        "structured": {"total": 1, "enabled": 1},
        "other": {"total": 0, "enabled": 0},
    }
    assert output["shared"] == {
        "total": 0,
        "enabled": 0,
        "single_value": {"total": 0, "enabled": 0},
        "structured": {"total": 0, "enabled": 0},
        "other": {"total": 0, "enabled": 0},
    }
    _assert_no_instance_details(output, "structured-id", "alice-mail", "structured_v1", "imap_password")


async def test_inventory_visibility_and_legacy_choice_order_use_existing_permissions(inventory):
    inventory.add_all([
        _secret("owned-scalar", "duplicate-key"),
        _secret("owned-expired", "aaa-expired", expires_at=PAST),
        _secret("owned-disabled", "disabled", status="disabled"),
        _secret("owned-structured", "000-structured", credential_format="structured_v1", status="expired"),
        _secret("owned-future", "000-other", credential_format="future"),
        _secret("owned-variable", "aaa-variable", secret_type="variable"),
        _secret("owned-disabled-var", "000-disabled-var", secret_type="variable", status="disabled"),
        _secret("someone-elses", "000-private", owner_id="202"),
        _secret("dept-scalar", "duplicate-key", owner_scope="system", owner_id=""),
        _secret("all-structured", "shared-structured", owner_scope="system", owner_id="", credential_format="structured_v1"),
        _secret("direct-other", "other-format", owner_scope="system", owner_id="", credential_format="future", status="disabled"),
        _secret("no-grant", "000-ungranted", owner_scope="system", owner_id=""),
        _secret("expired-grant", "000-grant-expired", owner_scope="system", owner_id=""),
        _secret("other-grantee", "000-grant-to-other", owner_scope="system", owner_id=""),
        _secret("other-dept", "000-grant-to-other-dept", owner_scope="system", owner_id=""),
        _grant("dept-scalar", "dept", DEPARTMENT),
        _grant("all-structured", "all", ""),
        _grant("all-structured", "user", USER_ID),  # 同一系统凭据命中两次授权，库存仍只计一次。
        _grant("direct-other", "user", USER_ID),
        _grant("expired-grant", "user", USER_ID, expires_at=PAST),
        _grant("other-grantee", "user", 202),
        _grant("other-dept", "dept", "different-dept"),
    ])
    inventory.commit()

    output = await _catalog()
    assert (output["status"], output["total"], output["enabled"]) == ("success", 8, 5)
    assert output["owned"] == {
        "total": 5,
        "enabled": 3,
        "single_value": {"total": 3, "enabled": 2},
        "structured": {"total": 1, "enabled": 0},
        "other": {"total": 1, "enabled": 1},
    }
    assert output["shared"] == {
        "total": 3,
        "enabled": 2,
        "single_value": {"total": 1, "enabled": 1},
        "structured": {"total": 1, "enabled": 1},
        "other": {"total": 1, "enabled": 0},
    }
    assert output["legacy_choices"] == {
        "total": 4,
        "items": [{"ref": "c1"}, {"ref": "c2"}, {"ref": "c3"}, {"ref": "c4"}],
        "truncated": False,
    }
    assert output["truncated"] is False
    _assert_no_instance_details(output, "000-private", "000-ungranted", "duplicate-key", DEPARTMENT)

    selected = await _resolve_choices(
        {"past_expiry": "c1", "legacy_variable": "c2", "shared": "c3", "owned": "c4", "out_of_range": "c5"},
        user_id=str(USER_ID),
        dept_code=DEPARTMENT,
    )
    assert selected == {
        "past_expiry": "owned-expired",
        "legacy_variable": "owned-variable",
        "shared": "dept-scalar",
        "owned": "owned-scalar",
    }


async def test_limit_truncates_only_choice_refs_and_keyword_is_ignored(inventory):
    inventory.add_all([
        _secret("a", "legacy-a"),
        _secret("b", "legacy-b"),
        _secret("c", "structured", credential_format="structured_v1"),
    ])
    inventory.commit()

    raw = await catalog.list_my_credentials.ainvoke({"limit": 1, "keyword": "missing-keyword"})
    output = json.loads(raw)
    baseline = await _catalog()
    assert output["legacy_choices"] == {"total": 2, "items": [{"ref": "c1"}], "truncated": True}
    assert baseline["legacy_choices"] == {"total": 2, "items": [{"ref": "c1"}, {"ref": "c2"}], "truncated": False}
    for key in ("total", "enabled", "owned", "shared"):
        assert output[key] == baseline[key]
    assert (output["total"], output["enabled"]) == (3, 3)
    assert output["truncated"] is False
    assert "items" not in output
    assert "choice_total" not in output

    envelope = build_tool_result_envelope(call_id="inventory-fixture", producer="list_my_credentials", result=raw)
    assert envelope.truncated is False
    assert envelope.evidence_eligible
    _assert_no_instance_details(output, "missing-keyword")


async def test_empty_visible_inventory_is_a_successful_zero(inventory):
    output = await _catalog()
    assert (output["status"], output["total"], output["enabled"]) == ("success", 0, 0)
    assert output["legacy_choices"] == {"total": 0, "items": [], "truncated": False}
    assert output["truncated"] is False


@pytest.mark.parametrize("cause", ["no_identity", "deleted_user", "disabled", "query_failure"])
async def test_unavailable_inventory_does_not_report_a_successful_zero(inventory, monkeypatch, cause):
    if cause == "no_identity":
        monkeypatch.setattr(catalog, "get_current_agent_context", lambda: None)
    elif cause == "deleted_user":
        inventory.delete(inventory.get(User, USER_ID))
        inventory.commit()
    elif cause == "disabled":
        async def disabled_settings():
            return {"secret_policy_allow_catalog": {"value": "false"}}

        monkeypatch.setattr(ConfigService, "get_all_from_db", staticmethod(disabled_settings))
    else:
        # 确认数据库查询真失败，避免用伪造空列表掩盖错误。
        inventory.rollback()
        UserSecret.__table__.drop(inventory.get_bind())

    raw = await catalog.list_my_credentials.ainvoke({})
    output = json.loads(raw)
    assert output["status"] == "unavailable"
    assert output["success"] is False
    assert not {"total", "enabled", "owned", "shared", "legacy_choices"} & output.keys()
    assert classify_evidence_result(output) == EvidenceStatus.UNAVAILABLE
    assert classify_evidence_result(raw) not in {EvidenceStatus.SUCCESS_EMPTY, EvidenceStatus.SUCCESS_NON_EMPTY}
    _assert_no_instance_details(output, "sys_secrets")
