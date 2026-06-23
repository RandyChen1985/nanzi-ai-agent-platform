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
    assert is_v1_api_whitelisted("/api/v1/users/profile") is False
