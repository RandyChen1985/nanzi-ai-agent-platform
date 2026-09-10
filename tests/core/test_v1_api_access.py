import pytest

pytestmark = pytest.mark.no_infrastructure


def test_build_api_resource_id():
    from app.core.v1_api_access import build_api_resource_id

    assert build_api_resource_id("get", "/api/v1/users/profile") == "GET:/api/v1/users/profile"


def test_expand_api_permission_candidates_includes_legacy_aliases():
    from app.core.v1_api_access import expand_api_permission_candidates

    canonical = "GET:/api/v1/users/profile"
    aliases = expand_api_permission_candidates(canonical)

    assert canonical in aliases
    assert "GET:/users/profile" in aliases
    assert "GET:/profile" in aliases


def test_legacy_api_grant_matches_canonical_runtime_id():
    from app.core.v1_api_access import expand_api_permission_candidates

    granted = {"GET:/profile"}
    runtime = "GET:/api/v1/users/profile"
    assert bool(granted & expand_api_permission_candidates(runtime))


def test_expand_api_permission_candidates_upgrades_short_path():
    from app.core.v1_api_access import expand_api_permission_candidates

    aliases = expand_api_permission_candidates("GET:/profile")

    assert "GET:/profile" in aliases
    assert "GET:/api/v1/profile" in aliases


def test_is_v1_api_whitelisted():
    from app.core.v1_api_access import is_v1_api_whitelisted

    assert is_v1_api_whitelisted("/api/v1/chat/completions") is True
    assert is_v1_api_whitelisted("/api/v1/chatbi/sql/execute") is False
    assert is_v1_api_whitelisted("/api/v1/tasks/123") is True
    assert is_v1_api_whitelisted("/api/v1/embed/tickets") is True
    assert is_v1_api_whitelisted("/api/v1/embed/tickets/exchange") is True
    assert is_v1_api_whitelisted("/api/v1/sandbox/docker/workspace/ensure") is True
    assert is_v1_api_whitelisted("/api/v1/sandbox/docker/workspace/status") is True
    assert is_v1_api_whitelisted("/api/v1/sandbox/k8s/workspace/ensure") is True
    assert is_v1_api_whitelisted("/api/v1/sandbox/k8s/workspace/status") is True
    assert is_v1_api_whitelisted("/api/v1/sandbox/k8s/workspace/stop") is True
    assert is_v1_api_whitelisted("/api/v1/sandbox/k8s/workspace/restart") is True
    assert is_v1_api_whitelisted("/api/v1/admin/sandbox/docker/prebuild") is False
    assert is_v1_api_whitelisted("/api/v1/users/profile") is False


def test_assignable_v1_api_resources_constant_has_three_entries():
    from app.core.v1_api_access import ASSIGNABLE_V1_API_RESOURCES

    assert len(ASSIGNABLE_V1_API_RESOURCES) == 3
    ids = {item["id"] for item in ASSIGNABLE_V1_API_RESOURCES}
    assert ids == {
        "GET:/api/v1/users/profile",
        "POST:/api/v1/schema",
        "POST:/api/v1/chatbi/sql/execute",
    }


def test_get_assignable_v1_api_resources_returns_static_list():
    from app.core.v1_api_access import get_assignable_v1_api_resources

    apis = get_assignable_v1_api_resources()

    assert len(apis) == 3
    assert {api["id"] for api in apis} == {
        "GET:/api/v1/users/profile",
        "POST:/api/v1/schema",
        "POST:/api/v1/chatbi/sql/execute",
    }
    assert apis[0] is not get_assignable_v1_api_resources()[0]
