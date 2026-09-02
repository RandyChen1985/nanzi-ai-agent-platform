from pathlib import Path

import pytest

from app.api import mcp_platform


pytestmark = pytest.mark.no_infrastructure


def test_platform_mcp_exposes_standard_oauth_oidc_discovery_and_token_routes():
    paths = {getattr(route, "path", None) for route in mcp_platform.router.routes}

    assert "/oauth/authorize" in paths
    assert "/oauth/token" in paths
    assert "/oauth/revoke" in paths
    assert "/.well-known/oauth-authorization-server" in paths
    assert "/.well-known/oauth-protected-resource" in paths
    assert "/.well-known/oauth-protected-resource/mcp/platform" in paths


def test_platform_mcp_token_endpoint_supports_user_authorization_and_refresh():
    source = Path("app/api/mcp_platform.py").read_text(encoding="utf-8")

    assert "client_secret_basic" in source
    assert "client_secret_post" in source
    assert "authorization_code" in source
    assert "refresh_token" in source
    assert "client_credentials" not in source


def test_platform_mcp_authorization_redirect_preserves_login_return_url():
    source = Path("app/api/mcp_platform.py").read_text(encoding="utf-8")

    assert 'f"/login?' in source
    assert "request.url.path" in source
    assert "request.url.query" in source


def test_main_mounts_platform_mcp_before_spa_catch_all_and_hosts_oauth_routes():
    source = Path("app/main.py").read_text(encoding="utf-8")

    assert 'app.mount("/mcp", platform_mcp.streamable_http_app())' in source
    assert "app.include_router(mcp_platform_router)" in source
    assert "platform_mcp_lifespan" in source


def test_platform_mcp_uses_public_url_for_transport_security():
    source = Path("app/services/mcp/platform_mcp.py").read_text(encoding="utf-8")

    assert "build_mcp_transport_security" in source
    assert "transport_security=build_mcp_transport_security(settings.APP_PUBLIC_URL)" in source


def test_platform_mcp_uses_the_canonical_resource_uri_at_oauth_boundaries():
    source = Path("app/api/mcp_platform.py").read_text(encoding="utf-8")

    assert "platform_mcp_resource_url()" in source
    assert "resource does not match Platform MCP" in source
    assert 'resource or MCP_RESOURCE' not in source


def test_oauth_authorization_post_rechecks_client_authorization_code_grant():
    source = Path("app/api/mcp_platform.py").read_text(encoding="utf-8")

    assert source.count('"authorization_code" not in normalize_scopes(client.allowed_grant_types)') >= 2
