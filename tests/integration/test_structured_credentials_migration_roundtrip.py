"""在随机临时库重放实际 SQL，验证列、索引以及已有数据。"""

from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
import sys

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from app.models.credential_type import McpCredentialUseGrant, StructuredCredential, TaskCredentialWait
from tests.temporary_database import init_infrastructure, temporary_database  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def _sql_files(dialect):
    mysql = dialect == "mysql"
    folder = ROOT / ("db-prod" if mysql else "db-prod-pg")
    spec = spec_from_file_location(f"migration_runner_{dialect}", folder / "apply_sql.py")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    baseline = (
        ("V1-create_system_configs.sql", "V43-create_mcp_tables.sql", "V209-add-credential-center.sql")
        if mysql else ("V0-baseline.sql", "V109-add-credential-center.sql")
    )
    tables = {"system_configs", "sys_mcp_tool_cache", "sys_mcp_servers", "sys_secrets", "sys_secret_bindings"}
    statements = []
    for name in baseline:
        for statement in module.split_sql_statements((folder / name).read_text(encoding="utf-8")):
            plain = re.sub(r"(?m)^\s*--[^\n]*", "", statement).strip()
            match = re.match(r'CREATE TABLE IF NOT EXISTS ["\x60]?(\w+)', plain, re.IGNORECASE)
            if match and match[1] in tables:
                statements.append(statement)
    assert len(statements) == len(tables)
    prefix = 218 if mysql else 118
    return (
        statements,
        module.split_sql_statements((folder / f"V{prefix}-add-structured-credentials.sql").read_text(encoding="utf-8")),
        folder / f"V{prefix + 1}-align-structured-credential-storage.sql",
        module.split_sql_statements,
    )


async def _apply(connection, statements):
    for statement in statements:
        # 历史 SQL 使用的 PREPARE 块在同一连接内执行。
        await connection.exec_driver_sql(statement, execution_options={"no_parameters": True})


async def _baseline(connection):
    baseline, feature, migration, split = _sql_files(connection.dialect.name)
    await _apply(connection, [*baseline, *feature])
    return migration, split


_CIPHERTEXT = "s" * ((256 * 1024 + 30) * 4 // 3 + 4)
_TIMESTAMP = datetime(2026, 11, 14, 3, 0, 1, 234567)


async def _insert_ciphertext(connection):
    await connection.execute(text("""
        INSERT INTO sys_secret_structured_payloads
            (secret_id, owner_scope, owner_id, type_key, type_version, account, account_digest,
             value_revision, key_id, payload_encrypted, public_projection, secret_fields_set, extra_schema)
        VALUES (:id, 'user', '42', 'api_key', 1, :account, :digest, 1, 'synthetic', :value, '{}', '[]', '{}')
    """), [
        {"id": "payload-1", "account": "Example", "digest": "a" * 64, "value": _CIPHERTEXT},
        {"id": "payload-2", "account": "example", "digest": "b" * 64, "value": "synthetic-small"},
    ])


async def _check_storage(connection):
    timestamp_fields = ("expires_at", "created_at", "resume_lease_until", "resolved_at")
    await connection.execute(text("""
        INSERT INTO sys_task_credential_waits
            (request_id, task_id, user_id, run_conversation_id, task_definition_digest, trace_id, status,
             expires_at, created_at, resume_lease_until, resolved_at)
        VALUES ('request-test', 1, 42, 'conv-test', 'digest-test', 'trace-test', 'pending', :t, :t, :t, :t)
    """), {"t": _TIMESTAMP})
    row = (await connection.exec_driver_sql(
        "SELECT " + ", ".join(timestamp_fields) + " FROM sys_task_credential_waits WHERE request_id='request-test'"
    )).one()
    assert tuple(row) == (_TIMESTAMP,) * len(timestamp_fields)
    for table, index, columns in (
        ("sys_task_credential_waits", "ix_task_credential_waits_task", ["task_id", "status", "expires_at"]),
        ("sys_mcp_credential_use_grants", "ix_mcp_credential_use_grants_secret", ["secret_id"]),
    ):
        found = await connection.run_sync(lambda conn: inspect(conn).get_indexes(table))
        assert next(i for i in found if i["name"] == index)["column_names"] == columns
    assert (await connection.exec_driver_sql(
        "SELECT payload_encrypted FROM sys_secret_structured_payloads WHERE secret_id='payload-1'"
    )).scalar_one() == _CIPHERTEXT
    assert (await connection.exec_driver_sql(
        "SELECT count(*) FROM sys_secret_structured_payloads WHERE owner_id='42'"
    )).scalar_one() == 2


@pytest.mark.parametrize("partially_upgraded", [False, True])
async def test_migration_replays_with_legacy_rows_and_matches_mcp_columns(partially_upgraded):
    async with temporary_database() as engine, engine.begin() as connection:
        migration, split = await _baseline(connection)
        mysql = connection.dialect.name == "mysql"
        if mysql:
            # 固定复现两种 _ci 列：不依赖测试服务器的 utf8mb4 默认 collation。
            await connection.exec_driver_sql("""
                ALTER TABLE sys_mcp_credential_profiles
                    MODIFY tool_id VARCHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL,
                    MODIFY server_id VARCHAR(36) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL
            """)
        if partially_upgraded:
            drop = "DROP INDEX ix_task_credential_waits_task"
            await connection.exec_driver_sql(drop + (" ON sys_task_credential_waits" if mysql else ""))
            if mysql:
                await connection.exec_driver_sql("ALTER TABLE sys_secret_structured_payloads MODIFY payload_encrypted TEXT NOT NULL")
        else:
            await _insert_ciphertext(connection)

        await connection.exec_driver_sql("""
            INSERT INTO sys_mcp_servers (id, server_name, sse_url) VALUES ('server-test', 'Fixture', 'https://example.test')
        """)
        await connection.exec_driver_sql("""
            INSERT INTO sys_mcp_tool_cache (id, server_id, tool_name) VALUES ('tool-test', 'server-test', 'Fixture:test')
        """)
        await connection.exec_driver_sql("""
            INSERT INTO sys_mcp_credential_profiles
                (tool_id, profile_id, server_id, tool_name, type_key, type_version, selection_mode, mappings,
                 schema_digest, endpoint_digest, revision, updated_at)
            VALUES ('tool-test', 'profile-test', 'server-test', 'Fixture:test', 'api_key', 1, 'fixed', '{}',
                    'schema-test', 'endpoint-test', 1, CURRENT_TIMESTAMP)
        """)
        join_sql = """
            SELECT p.profile_id FROM sys_mcp_credential_profiles p
                JOIN sys_mcp_tool_cache t ON t.id=p.tool_id AND t.server_id=p.server_id
                JOIN sys_mcp_servers s ON s.id=t.server_id
                JOIN sys_credential_type_versions v ON v.type_key=p.type_key AND v.version=p.type_version
        """
        if mysql:
            with pytest.raises(DBAPIError) as error:
                await connection.exec_driver_sql(join_sql)
            assert error.value.orig.args[0] == 1267
        assert migration.is_file(), f"缺少用于已部署环境的独立修复迁移：{migration.name}"
        sql = split(migration.read_text(encoding="utf-8"))
        await _apply(connection, sql)
        await _apply(connection, sql)
        if partially_upgraded:
            await _insert_ciphertext(connection)
        joined = await connection.exec_driver_sql(join_sql)
        assert joined.scalar_one() == "profile-test"
        await _check_storage(connection)


async def test_migration_fails_on_an_incompatible_index_name():
    async with temporary_database() as engine, engine.begin() as connection:
        migration, split = await _baseline(connection)
        mysql = connection.dialect.name == "mysql"
        drop = "DROP INDEX ix_task_credential_waits_task"
        await connection.exec_driver_sql(drop + (" ON sys_task_credential_waits" if mysql else ""))
        await connection.exec_driver_sql("CREATE INDEX ix_task_credential_waits_task ON sys_task_credential_waits(user_id)")
        assert migration.is_file(), "缺少修复迁移"
        with pytest.raises(DBAPIError) as error:
            await _apply(connection, split(migration.read_text(encoding="utf-8")))
        assert "ix_task_credential_waits_task" in str(error.value.orig)
        if mysql:
            assert error.value.orig.args[0] == 3819
        else:
            assert error.value.orig.sqlstate == "P0001"


async def test_mysql_preflight_rejects_colliding_ids_before_converting_columns():
    async with temporary_database() as engine, engine.begin() as connection:
        if connection.dialect.name != "mysql":
            pytest.skip("仅 MySQL 变更 MCP ID 列的 collation")
        migration, split = await _baseline(connection)
        await connection.exec_driver_sql(
            "ALTER TABLE sys_mcp_credential_profiles MODIFY tool_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL"
        )
        await connection.exec_driver_sql("""
            INSERT INTO sys_mcp_credential_profiles
                (tool_id, profile_id, server_id, tool_name, type_key, type_version, selection_mode, mappings,
                 schema_digest, endpoint_digest, revision, updated_at)
            VALUES ('Tool', 'profile-one', 'server-test', 'Fixture:test', 'api_key', 1, 'fixed', '{}',
                    'schema-test', 'endpoint-test', 1, CURRENT_TIMESTAMP),
                   ('tool', 'profile-two', 'server-test', 'Fixture:test', 'api_key', 1, 'fixed', '{}',
                    'schema-test', 'endpoint-test', 1, CURRENT_TIMESTAMP)
        """)
        with pytest.raises(DBAPIError) as error:
            await _apply(connection, split(migration.read_text(encoding="utf-8")))
        # 通用导入器会跳过 1062；预检必须给出不可跳过的错误。
        assert error.value.orig.args[0] == 3819
        assert "mcp_tool_id_collisions" in str(error.value.orig)
        assert (await connection.exec_driver_sql("SELECT COUNT(*) FROM sys_mcp_credential_profiles")).scalar_one() == 2


async def test_orm_created_tables_support_same_payload_timestamp_and_lookup_indexes():
    async with temporary_database(
        StructuredCredential.__table__, TaskCredentialWait.__table__, McpCredentialUseGrant.__table__,
    ) as engine, engine.begin() as connection:
        await _insert_ciphertext(connection)
        await _check_storage(connection)
