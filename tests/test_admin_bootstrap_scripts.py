from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.no_infrastructure


def test_admin_entrypoints_exist_for_mysql_and_postgresql():
    for directory in (ROOT / "db-prod", ROOT / "db-prod-pg"):
        for name in (
            "create-admin-user.sh",
            "create-admin-key.sh",
            "reset-admin-password.sh",
        ):
            wrapper = directory / name
            assert wrapper.exists(), wrapper
            assert "scripts/" in wrapper.read_text(encoding="utf-8")


def test_pg_baseline_wrapper_offers_admin_bootstrap_with_selected_connection():
    wrapper = (ROOT / "db-prod-pg" / "apply-sql.sh").read_text(encoding="utf-8")

    assert "create_admin_user.py" in wrapper
    assert 'DATABASE_TYPE=postgresql' in wrapper
    assert 'POSTGRES_PASSWORD="$PG_PASSWORD"' in wrapper
    assert "RUN_INIT_ADMIN" in wrapper


def test_pg_apply_wrapper_discovers_all_versioned_sql_files_in_order():
    wrapper = (ROOT / "db-prod-pg" / "apply-sql.sh").read_text(encoding="utf-8")

    assert 'SQL_FILES=()' in wrapper
    # 脚本改用 ls "$DB_DIR"/V*.sql | sort -V 收集迁移脚本，不再是 find -name
    assert 'ls "$DB_DIR"/V*.sql' in wrapper
    assert "sort -V" in wrapper
    # 筛选后的最终列表变量名为 FINAL_SQL_FILES
    assert 'for sql_file in "${FINAL_SQL_FILES[@]}"' in wrapper


def test_pg_apply_wrapper_only_bootstraps_admin_when_baseline_is_included():
    wrapper = (ROOT / "db-prod-pg" / "apply-sql.sh").read_text(encoding="utf-8")

    assert 'BASELINE_INCLUDED=false' in wrapper
    assert 'basename "$sql_file"' in wrapper
    assert 'BASELINE_INCLUDED=true' in wrapper


def test_admin_scripts_use_shared_selected_database_runtime():
    for name in ("create_admin_user.py", "create_admin_key.py", "reset_admin_password.py"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "AsyncSessionLocal" in source
        assert "UserRoleRelation" in source
        assert "aiomysql" not in source


def test_reset_admin_password_validates_confirmation_and_minimum_length():
    from scripts.reset_admin_password import validate_password

    assert validate_password("secret1", "secret1") == "secret1"

    with pytest.raises(ValueError, match="至少 6 个字符"):
        validate_password("12345", "12345")
    with pytest.raises(ValueError, match="两次输入的密码不一致"):
        validate_password("secret1", "secret2")
