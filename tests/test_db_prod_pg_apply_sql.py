import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


def load_pg_apply_sql_module():
    path = Path(__file__).resolve().parents[1] / "db-prod-pg" / "apply_sql.py"
    spec = importlib.util.spec_from_file_location("db_prod_pg_apply_sql", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pg_parse_args_requires_explicit_database(monkeypatch):
    module = load_pg_apply_sql_module()
    monkeypatch.delenv("PGDATABASE", raising=False)

    with pytest.raises(SystemExit):
        module.parse_args(["db-prod-pg/V0-baseline.sql"])


def test_pg_parse_args_accepts_list_tables_without_sql_file():
    module = load_pg_apply_sql_module()

    args = module.parse_args(
        [
            "--list-tables",
            "--host",
            "127.0.0.1",
            "--user",
            "postgres",
            "--password",
            "secret",
            "--database",
            "nanzi_demo_pg",
        ]
    )

    assert args.list_tables is True
    assert args.file_path is None


def test_pg_split_sql_skips_database_switching_statements():
    module = load_pg_apply_sql_module()

    statements = module.split_sql_statements(
        """
        CREATE DATABASE nanzi_demo_pg;
        \\c nanzi_demo_pg;
        CREATE TABLE ai_agent_users (id BIGINT PRIMARY KEY);
        """
    )

    assert statements == [
        "CREATE TABLE ai_agent_users (id BIGINT PRIMARY KEY)",
    ]


def test_pg_split_sql_preserves_dollar_quotes():
    module = load_pg_apply_sql_module()

    sql_content = """
    CREATE OR REPLACE FUNCTION test_func() RETURNS void AS $$
    BEGIN
        SELECT 1;
        UPDATE users SET status = 1;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TABLE next_table (id INT);
    """
    statements = module.split_sql_statements(sql_content)
    assert len(statements) == 2
    assert "BEGIN\n        SELECT 1;\n        UPDATE users SET status = 1;\n    END;" in statements[0]
    assert statements[1] == "CREATE TABLE next_table (id INT)"


def test_pg_apply_sql_sh_help():
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["bash", "db-prod-pg/apply-sql.sh", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "NanZi AI Agent Platform - PostgreSQL 数据库迁移工具" in res.stdout
    assert "--all" in res.stdout
    assert "--spec" in res.stdout
    assert "--last" in res.stdout


def test_pg_apply_sql_sh_interactive_cancel():
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["bash", "db-prod-pg/apply-sql.sh"],
        cwd=root,
        input="q\n",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "已取消执行" in res.stdout


def test_pg_apply_sql_sh_spec_range():
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["bash", "db-prod-pg/apply-sql.sh", "--spec", "v0-v56"],
        cwd=root,
        input="\n\n\n\n\n",  # 让交互式输入直接结束
        capture_output=True,
        text=True,
    )
    assert "本次选中的 SQL 迁移脚本" in res.stdout
    assert "db-prod-pg/V0-baseline.sql" in res.stdout


def test_pg_apply_sql_sh_last_no_record(tmp_path):
    root = Path(__file__).resolve().parents[1]
    record_file = root / "db-prod-pg" / ".last_applied_sql"
    backup = None
    if record_file.exists():
        backup = record_file.read_text(encoding="utf-8")
        record_file.unlink()

    try:
        res = subprocess.run(
            ["bash", "db-prod-pg/apply-sql.sh", "--last"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "未检测到上次执行记录文件" in res.stderr
    finally:
        if backup is not None:
            record_file.write_text(backup, encoding="utf-8")


def test_pg_apply_sql_native_sh_help():
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["bash", "db-prod-pg/apply-sql-native.sh", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "NanZi AI Agent Platform - PostgreSQL 原生迁移执行工具" in res.stdout
    assert "--all" in res.stdout
    assert "--spec" in res.stdout
    assert "--last" in res.stdout


def test_pg_apply_sql_native_sh_interactive_cancel():
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["bash", "db-prod-pg/apply-sql-native.sh"],
        cwd=root,
        input="q\n",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "已取消执行" in res.stdout


def test_pg_apply_sql_native_sh_spec_range():
    root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["bash", "db-prod-pg/apply-sql-native.sh", "--spec", "0-56"],
        cwd=root,
        input="\n\n\n\n\n",
        capture_output=True,
        text=True,
    )
    assert "本次选中的 SQL 迁移脚本" in res.stdout
    assert "db-prod-pg/V0-baseline.sql" in res.stdout


def test_pg_apply_sql_native_sh_last_no_record():
    root = Path(__file__).resolve().parents[1]
    record_file = root / "db-prod-pg" / ".last_applied_sql"
    backup = None
    if record_file.exists():
        backup = record_file.read_text(encoding="utf-8")
        record_file.unlink()

    try:
        res = subprocess.run(
            ["bash", "db-prod-pg/apply-sql-native.sh", "--last"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "未检测到上次执行记录文件" in res.stderr
    finally:
        if backup is not None:
            record_file.write_text(backup, encoding="utf-8")


def test_pg_apply_sql_native_sh_last_with_record():
    root = Path(__file__).resolve().parents[1]
    record_file = root / "db-prod-pg" / ".last_applied_sql"
    backup = None
    if record_file.exists():
        backup = record_file.read_text(encoding="utf-8")

    try:
        record_file.write_text("V56-add_metadata_quality_score.sql\n", encoding="utf-8")
        res = subprocess.run(
            ["bash", "db-prod-pg/apply-sql-native.sh", "--last"],
            cwd=root,
            input="\n\n\n\n\n",
            capture_output=True,
            text=True,
        )
        assert "检测到上次记录的 SQL 脚本为: V56-add_metadata_quality_score.sql" in res.stdout
        assert "db-prod-pg/V56-add_metadata_quality_score.sql" in res.stdout
    finally:
        if backup is not None:
            record_file.write_text(backup, encoding="utf-8")
        elif record_file.exists():
            record_file.unlink()


def test_pg_sql_files_do_not_use_integer_for_system_configs_boolean():
    """PostgreSQL 严格类型校验：system_configs 表的 is_secret 必须是 boolean (FALSE/TRUE)，不能为整数 0/1。"""
    root = Path(__file__).resolve().parents[1]
    pg_dir = root / "db-prod-pg"
    bad_files = []

    for sql_file in sorted(pg_dir.glob("V*.sql")):
        content = sql_file.read_text(encoding="utf-8")
        if "system_configs" in content:
            # 检查是否有独立行的 0 或 1 作为字段值写入
            lines = content.splitlines()
            for idx, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped in ("0", "1", "0,", "1,") and not stripped.startswith("--"):
                    bad_files.append((sql_file.name, idx, stripped))

    assert not bad_files, f"发现 PostgreSQL SQL 脚本中使用整数作为布尔值: {bad_files}"


def test_pg_sql_files_use_if_not_exists_for_add_column():
    """PostgreSQL 迁移幂等性：所有 ADD COLUMN 必须包含 IF NOT EXISTS，支持重跑与断点重试。"""
    root = Path(__file__).resolve().parents[1]
    pg_dir = root / "db-prod-pg"
    non_idempotent = []

    for sql_file in sorted(pg_dir.glob("V*.sql")):
        content = sql_file.read_text(encoding="utf-8")
        # 匹配 ADD COLUMN 但缺少 IF NOT EXISTS
        for match in re.finditer(r"ADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)[^\n;]+", content, re.IGNORECASE):
            non_idempotent.append((sql_file.name, match.group(0).strip()))

    assert not non_idempotent, f"发现未包含 IF NOT EXISTS 的 ADD COLUMN: {non_idempotent}"


