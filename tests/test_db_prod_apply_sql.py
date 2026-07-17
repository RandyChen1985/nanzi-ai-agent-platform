import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


def load_apply_sql_module():
    path = Path(__file__).resolve().parents[1] / "db-prod" / "apply_sql.py"
    spec = importlib.util.spec_from_file_location("db_prod_apply_sql", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_args_requires_explicit_database_and_ignores_env(monkeypatch):
    module = load_apply_sql_module()
    monkeypatch.setenv("MYSQL_DB", "nanzi_ai_agent_platform")

    with pytest.raises(SystemExit):
        module.parse_args(["db-prod/V0-init_nanzi_ai_agent_metadata.sql"])


def test_split_sql_skips_database_switching_statements():
    module = load_apply_sql_module()

    statements = module.split_sql_statements(
        """
        SET NAMES utf8mb4;
        CREATE DATABASE IF NOT EXISTS nanzi_ai_agent_platform;
        USE nanzi_ai_agent_platform;
        CREATE TABLE ai_agent_users (id BIGINT PRIMARY KEY);
        """
    )

    assert statements == [
        "SET NAMES utf8mb4",
        "CREATE TABLE ai_agent_users (id BIGINT PRIMARY KEY)",
    ]


def test_confirmation_rejects_non_yes(monkeypatch, capsys):
    module = load_apply_sql_module()
    config = module.DbConfig(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="secret",
        database="nanzi_ai_agent_platform_init_test",
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    with pytest.raises(SystemExit):
        module.confirm_execution(config, "db-prod/V0-init_nanzi_ai_agent_metadata.sql")

    out = capsys.readouterr().out
    assert "nanzi_ai_agent_platform_init_test" in out
    assert "secret" not in out


def test_migrations_include_scheduler_job_store_table():
    db_prod = Path(__file__).resolve().parents[1] / "db-prod"
    sql = "\n".join(path.read_text(encoding="utf-8") for path in db_prod.glob("V*.sql"))

    assert "ai_agent_scheduler_jobs" in sql


def test_migrations_include_indexes_seen_in_current_schema():
    db_prod = Path(__file__).resolve().parents[1] / "db-prod"
    sql = "\n".join(path.read_text(encoding="utf-8") for path in db_prod.glob("V*.sql"))

    assert "idx_agent_created" in sql
    assert "idx_category_updated" in sql
