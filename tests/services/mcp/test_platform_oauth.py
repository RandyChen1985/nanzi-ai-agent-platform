from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects.sqlite import dialect

from app.services.mcp.platform_oauth import (
    ACCESS_TOKEN_TTL_SECONDS,
    MAX_ACCESS_TOKEN_TTL_SECONDS,
    MCP_RESOURCE,
    McpPrincipal,
    build_pkce_challenge,
    filter_requested_scopes,
    hash_secret,
    intersect_authorized_ids,
    verify_pkce,
)


pytestmark = pytest.mark.no_infrastructure


def test_pkce_s256_challenge_is_verified_without_storing_verifier():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    challenge = build_pkce_challenge(verifier)

    assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert verify_pkce(verifier, challenge, "S256") is True
    assert verify_pkce("wrong-verifier", challenge, "S256") is False
    assert verify_pkce(verifier, challenge, "plain") is False


def test_client_scopes_are_an_upper_bound_for_requested_scopes():
    assert filter_requested_scopes(
        "agent:list knowledge:search",
        ["agent:list", "agent:invoke", "knowledge:search"],
    ) == ["agent:list", "knowledge:search"]
    assert filter_requested_scopes(
        ["agent:list", "agent:list", "not-allowed"],
        ["agent:list"],
    ) == ["agent:list"]


def test_principal_keeps_client_and_user_identity_separate():
    principal = McpPrincipal(
        client_id="crm-system",
        user_id="123",
        scopes=("knowledge:search",),
        resource=MCP_RESOURCE,
        auth_type="user_delegated",
    )

    assert principal.client_id == "crm-system"
    assert principal.user_id == "123"
    assert principal.is_user_delegated is True


def test_secret_hash_is_one_way_and_time_independent_for_verification():
    secret = "mcp_secret_test_value"
    digest = hash_secret(secret)

    assert digest != secret
    assert hash_secret(secret) == digest
    assert hash_secret("another-secret") != digest


def test_access_token_expiry_is_explicit():
    now = datetime.utcnow()
    principal = McpPrincipal(
        client_id="crm-system",
        user_id="123",
        scopes=("agent:list",),
        resource=MCP_RESOURCE,
        auth_type="user_delegated",
        expires_at=now + timedelta(minutes=1),
    )

    assert principal.is_expired(now) is False
    assert principal.is_expired(now + timedelta(minutes=2)) is True


def test_custom_access_token_ttl_has_a_safe_bounded_range():
    from app.services.mcp.platform_oauth import resolve_access_token_ttl

    assert resolve_access_token_ttl(None) == ACCESS_TOKEN_TTL_SECONDS
    assert resolve_access_token_ttl(900) == 900
    assert resolve_access_token_ttl(30 * 24 * 60 * 60) == MAX_ACCESS_TOKEN_TTL_SECONDS

    with pytest.raises(ValueError):
        resolve_access_token_ttl(299)
    with pytest.raises(ValueError):
        resolve_access_token_ttl(MAX_ACCESS_TOKEN_TTL_SECONDS + 1)


def test_authorized_scope_can_be_narrowed_by_a_requested_resource():
    assert intersect_authorized_ids(
        ["kb-a", "kb-b"],
        ["kb-b", "kb-c"],
        ["kb-b", "kb-c"],
    ) == ["kb-b"]


def test_intersect_authorized_ids_distinguishes_none_and_empty_client_policy():
    assert intersect_authorized_ids(["a", "b"], None) == ["a", "b"]
    assert intersect_authorized_ids(["a", "b"], []) == []
    assert intersect_authorized_ids(None, ["a", "b"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_security_policy_changes_expire_unconsumed_authorization_codes():
    from app.services.mcp.platform_oauth import PlatformMcpOAuthService

    db = SimpleNamespace(execute=AsyncMock())
    invalidated_at = datetime.utcnow()

    await PlatformMcpOAuthService.invalidate_unconsumed_authorization_codes(
        db,
        client_id="client-id",
        invalidated_at=invalidated_at,
    )

    statement = db.execute.await_args.args[0]
    sql = str(statement.compile(dialect=dialect()))
    assert "sys_mcp_oauth_authorization_codes" in sql
    assert "expires_at" in sql
    assert "consumed_at" in sql
