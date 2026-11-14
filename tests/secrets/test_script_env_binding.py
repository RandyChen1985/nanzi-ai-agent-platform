from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.secrets import injection
from app.services.secrets.field_binding import target_digest, valid_script_env_name


class Value:
    def __init__(self, value):
        self.value = value

    def reveal(self):
        return self.value


def result(value, secret_id="one", ok=True):
    return SimpleNamespace(ok=ok, value=Value(value), secret_id=secret_id, denial="denied_by_grant")


def test_target_tracks_executable_configuration():
    base = dict(id="1", code="a", requirements=("one",), parameter_schema={"type": "object"})
    original = target_digest("script_tool", SimpleNamespace(**base), "env:KEY")
    for key, value in (("code", "b"), ("requirements", ("two",)), ("parameter_schema", {"type": "string"})):
        assert target_digest("script_tool", SimpleNamespace(**(base | {key: value})), "env:KEY") != original
    assert valid_script_env_name("API_KEY")
    assert not any(valid_script_env_name(x) for x in ("PATH", "PYTHONPATH", "LD_PRELOAD", "A-B", ""))
    assert valid_script_env_name("X" * 124)
    assert not valid_script_env_name("X" * 125)


@pytest.mark.asyncio
async def test_multiple_fields_count_one_credential(monkeypatch):
    monkeypatch.setattr(injection, "list_bound_slots", AsyncMock(return_value=["env:A", "env:B", "env:C", "env:D"]))
    monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=3))
    resolve = AsyncMock(side_effect=[result("x"), result(42), result(1.5), result(True)])
    monkeypatch.setattr(injection.secret_resolver, "resolve", resolve)
    config = SimpleNamespace(id="1", code="a", requirements=(), parameter_schema={})
    assert await injection.apply_script_env_bindings(config, user_info={"user_id": "u"}) == {"A": "x", "B": "42", "C": "1.5", "D": "true"}
    assert resolve.call_args.kwargs["target_binding_digest"] == target_digest("script_tool", config, "env:D")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, {}, [], float("nan"), float("inf"), "nul\0value"])
async def test_invalid_value_fails_closed(monkeypatch, bad):
    monkeypatch.setattr(injection, "list_bound_slots", AsyncMock(return_value=["env:A", "env:B"]))
    monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=3))
    monkeypatch.setattr(injection.secret_resolver, "resolve", AsyncMock(side_effect=[result("secret"), result(bad)]))
    with pytest.raises(injection.BindingLookupError, match="凭据绑定暂不可用"):
        await injection.apply_script_env_bindings(SimpleNamespace(id="1"), user_info={"user_id": "u"})


@pytest.mark.asyncio
async def test_denial_and_limit_fail_closed(monkeypatch):
    monkeypatch.setattr(injection, "list_bound_slots", AsyncMock(return_value=["env:A", "env:B"]))
    monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=1))
    resolve = AsyncMock(side_effect=[result("secret"), result("other", "two")])
    monkeypatch.setattr(injection.secret_resolver, "resolve", resolve)
    with pytest.raises(injection.BindingLookupError):
        await injection.apply_script_env_bindings(SimpleNamespace(id="1"), user_info={"user_id": "u"})
    resolve.side_effect = [result("secret"), result("", ok=False)]
    with pytest.raises(injection.BindingLookupError):
        await injection.apply_script_env_bindings(SimpleNamespace(id="1"), user_info={"user_id": "u"})


@pytest.mark.asyncio
async def test_structured_field_requires_current_target_and_binding_owner(monkeypatch):
    from app.services.secrets import field_binding
    parent = SimpleNamespace(id="s", owner_scope="system", owner_id=None)
    binding = SimpleNamespace(target_digest="old", value_field="token", slot="env:KEY", bound_by_user_id="")
    session = SimpleNamespace(get=AsyncMock())
    with pytest.raises(ValueError):
        await field_binding.resolve_field(session, parent, binding, user_id="u", expected_target="new")
    binding.target_digest = "new"
    binding.bound_by_user_id = "other-user"
    with pytest.raises(ValueError):
        await field_binding.resolve_field(session, parent, binding, user_id="u", expected_target="new")
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_failure_is_closed(monkeypatch):
    monkeypatch.setattr(injection, "_bindings_unavailable_until", float("inf"))
    with pytest.raises(injection.BindingLookupError):
        await injection.list_bound_slots("script_tool", "1", prefix="env:", user_id="u")


@pytest.mark.asyncio
async def test_exception_message_is_not_exposed(monkeypatch):
    monkeypatch.setattr(injection, "list_bound_slots", AsyncMock(return_value=["env:KEY"]))
    monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=3))
    monkeypatch.setattr(injection.secret_resolver, "resolve", AsyncMock(side_effect=ValueError("private-secret")))
    with pytest.raises(injection.BindingLookupError) as error:
        await injection.apply_script_env_bindings(SimpleNamespace(id="1"), user_info={"user_id": "u"})
    assert "private-secret" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.asyncio
async def test_no_id_and_no_bindings(monkeypatch):
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(injection, "list_bound_slots", lookup)
    assert await injection.apply_script_env_bindings(SimpleNamespace(id=None)) == {}
    lookup.assert_not_awaited()
    assert await injection.apply_script_env_bindings(SimpleNamespace(id="1"), user_info={"user_id": "u"}) == {}


@pytest.mark.asyncio
async def test_rotation_during_multiple_field_resolution_rejects_mixed_revision(monkeypatch):
    monkeypatch.setattr(injection, "list_bound_slots", AsyncMock(return_value=["env:A", "env:B"]))
    monkeypatch.setattr(injection, "_max_inject_per_call", AsyncMock(return_value=3))
    first, second = result("old-user"), result("new-password")
    first.value.value_revision, second.value.value_revision = 1, 2
    monkeypatch.setattr(injection.secret_resolver, "resolve", AsyncMock(side_effect=[first, second]))
    with pytest.raises(injection.BindingLookupError):
        await injection.apply_script_env_bindings(SimpleNamespace(id="1"), user_info={"user_id": "u"})


@pytest.mark.asyncio
async def test_structured_script_fields_validate_and_resolve_with_real_schema(monkeypatch):
    from app.models.credential_type import StructuredCredential
    from app.models.script_tool import SysScriptTool
    from app.services.secrets import field_binding
    from app.services.secrets.credential_schema import BUILTIN_SCHEMAS
    from app.services.ai.tools.script_tool_factory import config_from_row

    parent = SimpleNamespace(id="s", owner_scope="user", owner_id="u")
    payload = SimpleNamespace(type_key="imap_password", type_version=1, owner_scope="user", owner_id="u", extra_schema={}, value_revision=2)
    tool = SimpleNamespace(id="t", name="my_mail", source="custom", code="pass", is_active=True,
                           parameter_schema={}, requirements=[], timeout_seconds=30, language="python", display_name="mail", description="mail")
    async def get(model, identifier):
        return payload if model is StructuredCredential else tool if model is SysScriptTool else None
    session = SimpleNamespace(get=get)
    config = SimpleNamespace(require_enabled=lambda: None, cipher=lambda: object())
    monkeypatch.setattr(field_binding, "load_payload_config", AsyncMock(return_value=config))
    monkeypatch.setattr(field_binding.type_catalog, "get_version", AsyncMock(return_value=SimpleNamespace(schema_json=BUILTIN_SCHEMAS["imap_password"][1])))
    monkeypatch.setattr(field_binding.structured_payload, "decrypt_fields", lambda *_: {"username": "alice", "password": "fixture", "host": "mail.example.test", "port": 993})
    for field, variable, expected in [("username", "MAIL_USER", "alice"), ("password", "MAIL_PASSWORD", "fixture"), ("host", "MAIL_HOST", "mail.example.test"), ("port", "MAIL_PORT", 993)]:
        digest = await field_binding.validate_binding(session, parent, "script_tool", "t", "env:" + variable, field, "u")
        assert digest == target_digest("script_tool", config_from_row(tool), "env:" + variable)
        binding = SimpleNamespace(slot="env:" + variable, value_field=field, target_digest=digest, bound_by_user_id="u")
        selected = await field_binding.resolve_field(session, parent, binding, user_id="u", expected_target=digest)
        assert selected.reveal() == expected
    with pytest.raises(ValueError):
        await field_binding.validate_binding(session, parent, "script_tool", "t", "env:PATH", "password", "u")
    with pytest.raises(ValueError):
        await field_binding.validate_binding(session, parent, "script_tool", "t", "env:KEY", "missing", "u")


def test_current_actor_accepts_id_alias():
    assert injection._current_user({"id": "u", "dept_code": "d"}) == ("u", "d")


@pytest.mark.asyncio
async def test_script_rule_configuration_is_rejected_before_database_access():
    from app.services.secrets import center
    with pytest.raises(center.SecretValidationError, match="固定"):
        await center.bind_rule(object(), consumer_type="script_tool", consumer_id="t", slot="env:KEY",
                               rule={}, bound_by_user_id="u")
