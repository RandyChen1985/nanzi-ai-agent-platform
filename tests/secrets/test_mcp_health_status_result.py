"""JSONPath 是结果选择的唯一依据，不再按字段名、值或状态词过滤。"""

import pytest

from app.services.secrets.mcp_model_result import project_model_result

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("state", ["configured", "missing"])
@pytest.mark.parametrize("path", ["$", "$.checks", "$.checks.token", "$['checks']['token']"])
def test_health_token_status_is_visible_for_root_and_explicit_paths(state, path):
    source = {
        "status": "ok" if state == "configured" else "degraded",
        "service": "mail-mcp", "version": "test-version", "time": "2026-11-14T00:00:00+00:00",
        "uptime_seconds": 12.345, "checks": {"token": state, "mailbox": "ready"},
        "accounts": 2, "mcp_path": "/mcp",
    }
    result = project_model_result({"structured_content": source}, [path], protected_values=())
    if path == "$":
        expected = source
    elif path == "$.checks":
        expected = {"checks": {"token": state, "mailbox": "ready"}}
    else:
        expected = {"checks": {"token": state}}
    assert result == {"status": "success", "result_visibility": "projected", "data": expected}


def test_token_status_is_consistent_in_recursive_paths_and_filters():
    source = {"checks": [{"token": "configured"}, {"token": "missing"}]}
    result = project_model_result({"structured_content": source}, ["$..token"], protected_values=())
    assert result["data"] == source
    result = project_model_result(
        {"structured_content": source}, ["$.checks[?@.token == 'configured'].token"], protected_values=(),
    )
    assert result["data"] == {"checks": [{"token": "configured"}]}


@pytest.mark.parametrize("value", ["unknown-token-value", "configured-extra", " configured", "CONFIGURED", None, False, {}, ["configured"]])
@pytest.mark.parametrize("path", ["$", "$.checks.token"])
def test_selected_token_values_are_returned_unchanged(value, path):
    result = project_model_result(
        {"structured_content": {"checks": {"token": value}}}, [path], protected_values=(),
    )
    assert result == {"status": "success", "result_visibility": "projected", "data": {"checks": {"token": value}}}


@pytest.mark.parametrize("state", ["configured", "missing"])
@pytest.mark.parametrize("protection", ["argument", "header"])
def test_matching_credential_values_do_not_override_jsonpath_selection(state, protection):
    result = project_model_result(
        {"structured_content": {"checks": {"token": state}}}, ["$"],
        protected_values=(state,) if protection == "argument" else (),
        auth_headers={"Authorization": f"Bearer {state}"} if protection == "header" else {},
    )
    assert result["data"] == {"checks": {"token": state}}


@pytest.mark.parametrize("name", ["password", "auth", "credentials", "access_token", "__proto__", "constructor"])
def test_selected_names_are_not_subject_to_a_denylist(name):
    result = project_model_result(
        {"structured_content": {"checks": {name: "configured"}}}, ["$"], protected_values=(),
    )
    assert result["data"] == {"checks": {name: "configured"}}
    explicit = project_model_result(
        {"structured_content": {"checks": {name: "arbitrary-value"}}},
        [f"$.checks.{name}"], protected_values=("arbitrary-value",),
    )
    assert explicit["data"] == {"checks": {name: "arbitrary-value"}}


def test_jsonpath_filters_use_the_original_values():
    source = {"checks": [
        {"label": "secret", "token": "unknown-token-value"},
        {"label": "known-secret", "token": "configured"},
        {"label": "public-status", "token": "missing"},
    ]}
    result = project_model_result(
        {"structured_content": source}, ["$.checks[?@.token != 'anything'].label"], protected_values=("configured",),
    )
    assert result["data"] == {"checks": [
        {"label": "secret"}, {"label": "known-secret"}, {"label": "public-status"},
    ]}


def test_root_returns_all_keys_and_values_without_status_exceptions():
    source = {
        "status": "ok", "auth": {"token": "fixture-token", "password": "fixture-password"},
        "credentials": [None, False, {"cookie": "session=fixture-cookie"}],
        "fixture-password": "value-in-a-key", "unrelated": "plain-business-value",
    }
    result = project_model_result(
        {"structured_content": source}, ["$"], protected_values=("fixture-password",),
        target_url="https://mcp.test/rpc?token=fixture-token",
        auth_headers={"X-Auth": "compound values; no parsing is needed"},
    )
    assert result == {"status": "success", "result_visibility": "projected", "data": source}
