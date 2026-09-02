import importlib.util
import os
import shutil
import subprocess
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


def test_parse_args_accepts_charset_preflight_without_sql_file():
    module = load_apply_sql_module()

    args = module.parse_args(
        [
            "--check-charset",
            "--host",
            "127.0.0.1",
            "--user",
            "root",
            "--password",
            "secret",
            "--database",
            "nanzi_demo",
        ]
    )

    assert args.check_charset is True
    assert args.file_path is None


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


def test_split_sql_keeps_prepared_conditional_ddl_in_one_statement():
    module = load_apply_sql_module()

    statements = module.split_sql_statements(
        """
        SET @v71_sql = IF(1 = 1, 'ALTER TABLE t DROP PRIMARY KEY', 'SELECT 1');
        PREPARE v71_stmt FROM @v71_sql;
        EXECUTE v71_stmt;
        DEALLOCATE PREPARE v71_stmt;
        """
    )

    assert len(statements) == 1
    assert "SET @v71_sql" in statements[0]
    assert "PREPARE v71_stmt" in statements[0]
    assert "DEALLOCATE PREPARE v71_stmt" in statements[0]


def test_v71_uses_conditional_same_session_blocks_for_each_schema_change():
    module = load_apply_sql_module()
    sql_path = Path(__file__).resolve().parents[1] / "db-prod" / "V71-add_audit_log_partitions.sql"

    statements = module.split_sql_statements(sql_path.read_text(encoding="utf-8"))

    assert len(statements) == 13
    assert all("PREPARE v71_stmt" in statement for statement in statements[:-1])
    assert all("DEALLOCATE PREPARE v71_stmt" in statement for statement in statements[:-1])
    assert "INSERT IGNORE INTO `system_configs`" in statements[-1]


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


@pytest.mark.asyncio
async def test_inspect_database_charset_returns_existing_database_charset(monkeypatch):
    module = load_apply_sql_module()

    class FakeCursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, query, params):
            assert "information_schema.SCHEMATA" in query
            assert params == ("nanzi_demo",)

        async def fetchone(self):
            return ("latin1", "latin1_swedish_ci")

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

        async def ensure_closed(self):
            return None

    async def fake_connect(**kwargs):
        assert "db" not in kwargs
        return FakeConnection()

    monkeypatch.setattr(module.aiomysql, "connect", fake_connect)
    charset = await module.inspect_database_charset(
        module.DbConfig("127.0.0.1", 3306, "root", "secret", "nanzi_demo")
    )

    assert charset == ("latin1", "latin1_swedish_ci")


@pytest.mark.asyncio
async def test_inspect_database_charset_returns_none_when_database_is_missing(monkeypatch):
    module = load_apply_sql_module()

    class FakeCursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, query, params):
            return None

        async def fetchone(self):
            return None

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def close(self):
            return None

        async def ensure_closed(self):
            return None

    async def fake_connect(**kwargs):
        return FakeConnection()

    monkeypatch.setattr(module.aiomysql, "connect", fake_connect)
    charset = await module.inspect_database_charset(
        module.DbConfig("127.0.0.1", 3306, "root", "secret", "nanzi_demo")
    )

    assert charset is None


@pytest.mark.asyncio
async def test_check_database_charset_returns_mismatch_for_non_utf8mb4(monkeypatch, capsys):
    module = load_apply_sql_module()

    async def fake_inspect(_config):
        return ("latin1", "latin1_swedish_ci")

    monkeypatch.setattr(module, "inspect_database_charset", fake_inspect)
    status = await module.check_database_charset(
        module.DbConfig("127.0.0.1", 3306, "root", "secret", "nanzi_demo")
    )

    assert status == 2
    assert "不是 utf8mb4" in capsys.readouterr().out


def test_migrations_include_scheduler_job_store_table():
    db_prod = Path(__file__).resolve().parents[1] / "db-prod"
    sql = "\n".join(path.read_text(encoding="utf-8") for path in db_prod.glob("V*.sql"))

    assert "ai_agent_scheduler_jobs" in sql


def test_migrations_include_indexes_seen_in_current_schema():
    db_prod = Path(__file__).resolve().parents[1] / "db-prod"
    sql = "\n".join(path.read_text(encoding="utf-8") for path in db_prod.glob("V*.sql"))

    assert "idx_agent_created" in sql
    assert "idx_category_updated" in sql


def test_mysql_sql_execution_mode_seed_defaults_to_local():
    migration = (
        Path(__file__).resolve().parents[1] / "db-prod" / "V56-add_sql_execution_mode_to_system_configs.sql"
    ).read_text(encoding="utf-8")

    assert "默认值为 local" in migration
    assert "'sql_execution_mode', 'local'" in migration


def test_mysql_python_wrapper_resolves_relative_sql_from_db_prod_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql.sh"
    shutil.copy2(root / "db-prod" / "apply-sql.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    fake_python = temp_bin / "python3"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' 'fake python invoked' \"$@\"\n", encoding="utf-8")
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nyes\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "fake python invoked" in output
    assert str(temp_db_prod / "V0-test.sql") in output
    assert "File not found" not in output


def test_mysql_wrappers_default_blank_host_and_port_to_localhost(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql.sh"
    shutil.copy2(root / "db-prod" / "apply-sql.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    fake_python = temp_bin / "python3"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' 'fake python invoked' \"$@\"\n", encoding="utf-8")
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="\n\nroot\n\nnanzi_demo\nyes\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "Host     : localhost" in output
    assert "Port     : 3306" in output


def test_mysql_python_wrapper_rejects_non_utf8mb4_before_migration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql.sh"
    shutil.copy2(root / "db-prod" / "apply-sql.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    capture = tmp_path / "python-calls.log"
    fake_python = temp_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_PYTHON_CAPTURE\"\n"
        "case \"$*\" in\n"
        "  *--check-charset*) printf '%s\\n' '目标数据库默认字符集: latin1，排序规则: latin1_swedish_ci' '⚠️ 可能出现乱码。'; exit 2 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    env["FAKE_PYTHON_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nno\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "可能出现乱码" in output
    calls = capture.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 1
    assert "--check-charset" in calls[0]


def test_mysql_python_wrapper_continues_after_explicit_yes_for_non_utf8mb4(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql.sh"
    shutil.copy2(root / "db-prod" / "apply-sql.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    capture = tmp_path / "python-calls.log"
    fake_python = temp_bin / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_PYTHON_CAPTURE\"\n"
        "case \"$*\" in\n"
        "  *--check-charset*) printf '%s\\n' '目标数据库默认字符集: latin1，排序规则: latin1_swedish_ci' '⚠️ 可能出现乱码。'; exit 2 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    env["FAKE_PYTHON_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nyes\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = capture.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
    assert "--check-charset" in calls[0]
    assert "--check-charset" not in calls[1]


def test_mysql_native_wrapper_rejects_non_utf8mb4_before_migration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql-native.sh"
    shutil.copy2(root / "db-prod" / "apply-sql-native.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    capture = tmp_path / "mysql-calls.log"
    fake_mysql = temp_bin / "mysql"
    fake_mysql.write_text(
        "#!/bin/sh\n"
        "input=$(cat)\n"
        "printf '%s\\n--- invocation ---\\n' \"$input\" >> \"$FAKE_MYSQL_CAPTURE\"\n"
        "case \"$input\" in\n"
        "  *information_schema.SCHEMATA*) printf 'latin1\\tlatin1_swedish_ci\\n' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_mysql.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    env["FAKE_MYSQL_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nno\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "不是 utf8mb4" in output
    calls = capture.read_text(encoding="utf-8").split("\n--- invocation ---\n")
    assert len(calls) == 2
    assert "information_schema.SCHEMATA" in calls[0]
    assert "CREATE DATABASE" not in calls[0]
    assert "CREATE DATABASE" not in calls[1]


def test_mysql_native_wrapper_continues_after_explicit_yes_for_non_utf8mb4(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql-native.sh"
    shutil.copy2(root / "db-prod" / "apply-sql-native.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    capture = tmp_path / "mysql-calls.log"
    fake_mysql = temp_bin / "mysql"
    fake_mysql.write_text(
        "#!/bin/sh\n"
        "input=$(cat)\n"
        "printf '%s\\n--- invocation ---\\n' \"$input\" >> \"$FAKE_MYSQL_CAPTURE\"\n"
        "case \"$input\" in\n"
        "  *information_schema.SCHEMATA*) printf 'latin1\\tlatin1_swedish_ci\\n' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_mysql.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    env["FAKE_MYSQL_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nyes\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = capture.read_text(encoding="utf-8").split("\n--- invocation ---\n")
    assert len(calls) == 3
    assert "information_schema.SCHEMATA" in calls[0]
    assert "CREATE DATABASE" in calls[1]


def test_mysql_native_wrapper_resolves_relative_sql_from_db_prod_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql-native.sh"
    shutil.copy2(root / "db-prod" / "apply-sql-native.sh", wrapper)
    wrapper.chmod(0o755)
    (temp_db_prod / "V0-test.sql").write_text("-- test SQL\n", encoding="utf-8")

    fake_mysql = temp_bin / "mysql"
    fake_mysql.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_mysql.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(wrapper), "V0-test.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nyes\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "Reading" in output
    assert "V0-test.sql" in output
    assert "No such file" not in output


def test_mysql_native_wrapper_keeps_prepare_execute_in_one_session(tmp_path):
    root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "repo"
    temp_db_prod = temp_root / "db-prod"
    temp_bin = tmp_path / "bin"
    temp_db_prod.mkdir(parents=True)
    temp_bin.mkdir()

    wrapper = temp_db_prod / "apply-sql-native.sh"
    shutil.copy2(root / "db-prod" / "apply-sql-native.sh", wrapper)
    wrapper.chmod(0o755)
    shutil.copy2(
        root / "db-prod" / "V105-add_mcp_scope_and_user_id.sql",
        temp_db_prod / "V105-add_mcp_scope_and_user_id.sql",
    )

    fake_mysql = temp_bin / "mysql"
    fake_mysql.write_text(
        "#!/bin/sh\n"
        "input_file=$(mktemp)\n"
        "cat > \"$input_file\"\n"
        "cat \"$input_file\" >> \"$FAKE_MYSQL_CAPTURE\"\n"
        "printf '\\n--- invocation ---\\n' >> \"$FAKE_MYSQL_CAPTURE\"\n"
        "if grep -q 'EXECUTE stmt' \"$input_file\" && ! grep -q 'PREPARE stmt' \"$input_file\"; then\n"
        "  echo 'ERROR 1243 (HY000): Unknown prepared statement handler (stmt) given to EXECUTE' >&2\n"
        "  rm -f \"$input_file\"\n"
        "  exit 1\n"
        "fi\n"
        "rm -f \"$input_file\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_mysql.chmod(0o755)

    capture = tmp_path / "mysql-calls.log"
    env = os.environ.copy()
    env["PATH"] = f"{temp_bin}:{env['PATH']}"
    env["FAKE_MYSQL_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["bash", str(wrapper), "V105-add_mcp_scope_and_user_id.sql"],
        input="localhost\n3306\nroot\n\nnanzi_demo\nyes\n",
        text=True,
        capture_output=True,
        cwd=temp_db_prod,
        env=env,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Unknown prepared statement handler" not in output

    invocations = capture.read_text(encoding="utf-8").split("\n--- invocation ---\n")
    prepared_invocation = next(
        invocation for invocation in invocations if "PREPARE stmt" in invocation
    )
    assert "EXECUTE stmt" in prepared_invocation
    assert "DEALLOCATE PREPARE stmt" in prepared_invocation
    assert "PREPARE stmt FROM @sql;\nEXECUTE stmt;\nDEALLOCATE PREPARE stmt" in prepared_invocation
