"""结构化凭据合并迁移契约；只解析文件，不连接数据库。"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = {
    "mysql": ROOT / "db-prod/V218-add-structured-credentials.sql",
    "postgresql": ROOT / "db-prod-pg/V118-add-structured-credentials.sql",
}
EXPECTED_COLUMNS = {
    ("sys_secrets", "credential_format"),
    ("sys_secret_bindings", "value_field"),
    ("sys_secret_bindings", "target_digest"),
    ("sys_mcp_credential_profiles", "model_result_fields"),
    ("sys_mcp_tool_cache", "model_result_policy"),
}
EXPECTED_TABLES = {
    "sys_credential_types", "sys_credential_type_versions",
    "sys_secret_structured_payloads", "sys_mcp_credential_profiles",
    "sys_mcp_credential_use_grants", "sys_task_credential_waits",
}


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _statements(database):
    path = MIGRATIONS[database]
    assert path.is_file(), f"缺少合并迁移：{path.name}"
    importer = _load_module(f"structured_migration_{database}", path.parent / "apply_sql.py")
    statements = importer.split_sql_statements(path.read_text(encoding="utf-8"))
    # PostgreSQL 解析器保留行首注释；只移除注释，仍验证真实解析器返回的语句边界。
    return [re.sub(r"(?m)^\s*--[^\n]*", "", statement).strip() for statement in statements]


@pytest.mark.parametrize("database,previous_version", [("mysql", 217), ("postgresql", 117)])
def test_credential_migration_immediately_follows_main_branch_migrations(database, previous_version):
    migration = MIGRATIONS[database]
    previous = migration.parent / f"V{previous_version}-multi-notification-channels.sql"
    assert previous.is_file()
    assert migration.is_file()
    assert int(re.match(r"V(\d+)-", migration.name).group(1)) == previous_version + 1
    assert list(migration.parent.glob(f"V{previous_version + 1}-*.sql")) == [migration]


@pytest.mark.parametrize("database", MIGRATIONS)
def test_consolidated_migration_keeps_schema_and_account_grant_constraints(database):
    statements = _statements(database)
    tables = {
        re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", statement).group(1): statement
        for statement in statements if statement.startswith("CREATE TABLE")
    }
    assert set(tables) == EXPECTED_TABLES
    assert "PRIMARY KEY (type_key, version)" in tables["sys_credential_type_versions"]
    assert "UNIQUE (owner_scope, owner_id, type_key, type_version, account_digest)" in tables["sys_secret_structured_payloads"]
    assert "PRIMARY KEY (tool_id, secret_id, grantee_type, grantee_id)" in tables["sys_mcp_credential_use_grants"]
    assert "ix_task_credential_waits_task" in "\n".join(statements)
    assert not any(re.match(r"(?:DROP|DELETE|TRUNCATE|UPDATE)\b", statement, re.I) for statement in statements)


@pytest.mark.parametrize("database", MIGRATIONS)
def test_seed_schema_and_hash_match_runtime_without_overwriting_published_versions(database):
    statements = _statements(database)
    schema_module = _load_module("structured_migration_schemas", ROOT / "app/services/secrets/credential_schema.py")
    seeds = [statement for statement in statements if statement.startswith("INSERT INTO sys_credential_type_versions ")]
    assert len(seeds) == len(schema_module.BUILTIN_SCHEMAS) == 9
    keys = set()
    for seed in seeds:
        match = re.search(r"VALUES \('([^']+)', 1, '([^']+)', '([a-f0-9]{64})'\)", seed)
        assert match, seed
        key, schema_json, digest = match.groups()
        keys.add(key)
        schema = json.loads(schema_json)
        assert schema == schema_module.BUILTIN_SCHEMAS[key][1]
        canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert hashlib.sha256(canonical.encode()).hexdigest() == digest
        if database == "mysql":
            assert seed.endswith("ON DUPLICATE KEY UPDATE type_key = type_key")
        else:
            assert seed.endswith("ON CONFLICT (type_key, version) DO NOTHING")
    assert keys == set(schema_module.BUILTIN_SCHEMAS)


@pytest.mark.parametrize("database", MIGRATIONS)
def test_settings_seed_preserves_existing_values_and_environment_fallback(database):
    statements = _statements(database)
    seeds = [statement for statement in statements if re.match(r'INSERT(?: IGNORE)? INTO [`"]system_configs', statement)]
    assert len(seeds) == 1
    seed = seeds[0]
    assert "('structured_credentials_enabled', NULL," in seed
    assert "('credential_payload_keyring_json', NULL," in seed
    assert "'security', FALSE)" in seed
    assert "'security', TRUE)" in seed
    if database == "mysql":
        assert seed.startswith("INSERT IGNORE")
    else:
        assert seed.endswith('ON CONFLICT ("key") DO NOTHING')


def test_mysql_parser_keeps_all_replay_guards_in_same_session_blocks():
    statements = _statements("mysql")
    blocks = [statement for statement in statements if "PREPARE credential_stmt" in statement]
    assert len(blocks) == len(EXPECTED_COLUMNS)
    pairs = set()
    for block in blocks:
        table = re.search(r"TABLE_NAME\s*=\s*'([^']+)'", block).group(1)
        column = re.search(r"COLUMN_NAME\s*=\s*'([^']+)'", block).group(1)
        pairs.add((table, column))
        assert "information_schema.COLUMNS" in block
        assert "TABLE_SCHEMA=DATABASE()" in block
        assert "'SELECT 1', 'ALTER TABLE" in block
        assert "EXECUTE credential_stmt" in block
        assert "DEALLOCATE PREPARE credential_stmt" in block
        assert f"ADD COLUMN {column}" in block
    assert pairs == EXPECTED_COLUMNS
    assert not any(statement.startswith("ALTER TABLE") for statement in statements)
    assert len(statements) == 30


def test_postgres_parser_keeps_idempotent_columns_and_nullable_result_policy():
    statements = _statements("postgresql")
    alters = [statement for statement in statements if statement.startswith("ALTER TABLE")]
    pairs = set()
    for alter in alters:
        match = re.match(r"ALTER TABLE (\w+)\s+ADD COLUMN IF NOT EXISTS (\w+)", alter)
        assert match, alter
        pairs.add(match.groups())
    assert pairs == EXPECTED_COLUMNS
    assert any("credential_format VARCHAR(24) NOT NULL DEFAULT 'legacy'" in statement for statement in alters)
    assert any("model_result_fields JSONB NULL" in statement for statement in alters)
    assert any("model_result_policy JSONB NULL" in statement for statement in alters)
    assert len(statements) == 33


@pytest.mark.parametrize("database,old_version", [
    ("mysql", version) for version in range(216, 221)
] + [
    ("postgresql", version) for version in range(116, 121)
])
@pytest.mark.parametrize("explicit_path", [False, True])
def test_migration_selection_handles_old_checkpoints_without_running_sql(tmp_path, database, old_version, explicit_path):
    migration = MIGRATIONS[database]
    assert migration.is_file()
    folder = tmp_path / migration.parent.name
    folder.mkdir()
    wrapper = folder / "apply-sql.sh"
    shutil.copy2(migration.parent / "apply-sql.sh", wrapper)
    shutil.copy2(migration, folder / migration.name)
    (folder / "V1-earlier.sql").write_text("SELECT 1;\n", encoding="utf-8")
    record = folder / ".last_applied_sql"
    old_name = f"V{old_version}-removed-migration.sql\n"
    record.write_text(old_name, encoding="utf-8")
    # 禁止测试进入执行器；空用户名/库名会在选择清单之后停止，连预检也不运行。
    (folder / "apply_sql.py").write_text('raise RuntimeError("UNEXPECTED_SQL_EXECUTION")\n', encoding="utf-8")
    environment = os.environ.copy()
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment["PATH"]
    result = subprocess.run(
        ["bash", str(wrapper), str(folder / migration.name) if explicit_path else "--last"],
        cwd=tmp_path, input="\n" * 8,
        capture_output=True, text=True, env=environment, timeout=15,
    )
    output = result.stdout + result.stderr
    version = int(re.match(r"V(\d+)-", migration.name).group(1))
    if explicit_path or old_version <= version:
        assert "共 1 个" in output
        assert migration.name in output
    else:
        # 连续编号低于旧开发断点时，不改变通用导入器的选择规则；必须显式指定文件。
        assert "未找到与上次记录" in output
        assert migration.name not in output
    assert "V1-earlier.sql" not in output
    assert "UNEXPECTED_SQL_EXECUTION" not in output
    assert result.returncode != 0
    assert record.read_text(encoding="utf-8") == old_name
