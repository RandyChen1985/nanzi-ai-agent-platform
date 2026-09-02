from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).parents[1]


def test_mysql_platform_mcp_migration_creates_oauth_and_audit_tables_and_permissions():
    sql = (ROOT / "db-prod/V137-create_platform_mcp_oauth.sql").read_text(encoding="utf-8")

    for table in (
        "sys_mcp_oauth_clients",
        "sys_mcp_oauth_grants",
        "sys_mcp_oauth_authorization_codes",
        "sys_mcp_oauth_access_tokens",
        "sys_mcp_oauth_refresh_tokens",
        "sys_mcp_inbound_audit_logs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "platform_enabled" in sql
    assert "menu:mcp_service" in sql
    assert "client_secret_hash" in sql
    assert "WHERE NOT EXISTS" in sql
    assert sql.count(" COMMENT '") >= 80
    assert "CREATE TABLE IF NOT EXISTS sys_mcp_platform_config" in sql
    assert "INSERT IGNORE INTO sys_mcp_platform_config (id) VALUES (1)" in sql
    assert "INSERT IGNORE INTO system_configs" not in sql


def test_postgresql_platform_mcp_migration_matches_mysql_contract():
    sql = (ROOT / "db-prod-pg/V38-create_platform_mcp_oauth.sql").read_text(encoding="utf-8")

    assert "WHERE NOT EXISTS" in sql
    assert "sys_mcp_oauth_clients" in sql
    assert "sys_mcp_inbound_audit_logs" in sql
    assert "element:mcp_service:client:secret_reset" in sql
    assert sql.count("COMMENT ON COLUMN") >= 80
    assert "CREATE TABLE IF NOT EXISTS sys_mcp_platform_config" in sql


def test_security_audit_migrations_create_oauth_event_table():
    mysql_sql = (ROOT / "db-prod/V141-mcp-oauth-security-audit.sql").read_text(encoding="utf-8")
    pg_sql = (ROOT / "db-prod-pg/V42-mcp-oauth-security-audit.sql").read_text(encoding="utf-8")

    for sql in (mysql_sql, pg_sql):
        assert "sys_mcp_oauth_security_audit_logs" in sql
        assert "event_type" in sql
        assert "client_id" in sql
        assert "user_id" in sql
        assert "created_at" in sql
    assert mysql_sql.count(" COMMENT '") >= 11
    assert pg_sql.count("COMMENT ON COLUMN") >= 11
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "INSERT INTO system_configs" not in sql


def test_user_token_permission_is_seeded_by_follow_up_migrations():
    mysql = (ROOT / "db-prod/V138-add_mcp_user_access_token_permission.sql").read_text(encoding="utf-8")
    postgres = (ROOT / "db-prod-pg/V39-add_mcp_user_access_token_permission.sql").read_text(encoding="utf-8")

    for sql in (mysql, postgres):
        assert "element:mcp_service:client:token_issue" in sql
        assert "ai_agent_resource_permissions" in sql
        assert "WHERE NOT EXISTS" in sql


def test_scope_version_migration_tracks_client_scope_changes_and_token_issuance():
    mysql = (ROOT / "db-prod/V139-add_mcp_scope_version.sql").read_text(encoding="utf-8")
    postgres = (ROOT / "db-prod-pg/V40-add_mcp_scope_version.sql").read_text(encoding="utf-8")

    assert "sys_mcp_oauth_clients" in mysql
    assert "sys_mcp_oauth_access_tokens" in mysql
    assert "scope_version" in mysql
    assert "scope_version" in postgres
    assert "ADD COLUMN" in mysql
    assert "ADD COLUMN IF NOT EXISTS" in postgres
