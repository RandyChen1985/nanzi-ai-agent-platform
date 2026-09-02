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
    assert "ON CONFLICT (id) DO NOTHING" in sql
    assert "INSERT INTO system_configs" not in sql


def test_user_token_permission_is_seeded_by_follow_up_migrations():
    mysql = (ROOT / "db-prod/V138-add_mcp_user_access_token_permission.sql").read_text(encoding="utf-8")
    postgres = (ROOT / "db-prod-pg/V39-add_mcp_user_access_token_permission.sql").read_text(encoding="utf-8")

    for sql in (mysql, postgres):
        assert "element:mcp_service:client:token_issue" in sql
        assert "ai_agent_resource_permissions" in sql
        assert "WHERE NOT EXISTS" in sql


def test_client_owner_migrations_backfill_legacy_creator_usernames():
    mysql_path = ROOT / "db-prod/V139-backfill_mcp_client_owner_user_ids.sql"
    postgres_path = ROOT / "db-prod-pg/V40-backfill_mcp_client_owner_user_ids.sql"
    mysql = mysql_path.read_text(encoding="utf-8")
    postgres = postgres_path.read_text(encoding="utf-8")

    for sql in (mysql, postgres):
        assert "sys_mcp_oauth_clients" in sql
        assert "ai_agent_users" in sql
        assert "created_by" in sql
        assert "user_name" in sql
        assert "id" in sql
        assert "legacy" in sql.lower()

    assert "REGEXP" in mysql
    assert "!~" in postgres
