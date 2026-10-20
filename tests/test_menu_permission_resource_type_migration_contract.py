"""菜单权限 resource_type 修复迁移契约（MySQL V175 / PG V76）。

用户反馈「明明勾了系统配置页面，侧栏菜单不显示」的根因是历史脏数据：
旧前端勾菜单时级联把 `menu:*` 写成了 `resource_type='element'`。修复分两层：
读取层前缀归位（`_aggregate_permissions`）+ 本迁移清理存量。这里钉住迁移写法，
防止后续改坏成「误删非菜单权限」或「MySQL 用了会 1093 的子查询 DELETE」。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]
MYSQL = ROOT / "db-prod" / "V175-fix_menu_permission_resource_type.sql"
PG = ROOT / "db-prod-pg" / "V76-fix_menu_permission_resource_type.sql"


def _sql_only(path: Path) -> str:
    """剥掉 `--` 注释行，只留可执行 SQL（注释里必然会出现 menu:% 等字面量）。"""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )


def test_both_migration_files_exist():
    assert MYSQL.is_file(), f"缺少 MySQL 迁移: {MYSQL}"
    assert PG.is_file(), f"缺少 PostgreSQL 迁移: {PG}"


def test_mysql_deletes_duplicates_before_flipping_type():
    sql = _sql_only(MYSQL)
    # 必须先删重复再 UPDATE：库里可能同时存在 menu 行与脏 element 行，
    # 直接 UPDATE 会让同一 user/role 出现两条同 id 的 menu 授权。
    assert "DELETE dup FROM ai_agent_resource_permissions dup" in sql
    assert "JOIN ai_agent_resource_permissions keep" in sql
    assert sql.index("DELETE dup FROM") < sql.index("UPDATE ai_agent_resource_permissions")
    # NULL 归属（user_id 与 role_id 二者必有一 NULL）必须用 <=>，否则匹配不上。
    assert "keep.user_id <=> dup.user_id" in sql
    assert "keep.role_id <=> dup.role_id" in sql
    # 只在 enabled 一致时删重复：一启用一禁用时删掉启用行会改变有效授权
    assert "keep.enabled = dup.enabled" in sql


def test_mysql_only_touches_menu_rows_mislabeled_as_elements():
    sql = _sql_only(MYSQL)
    assert "SET resource_type = 'menu'" in sql
    # SET 子句只改类型与时间戳，绝不碰 enabled / 归属列（否则会改变授权语义）
    update_sql = sql.split("UPDATE ai_agent_resource_permissions")[1]
    set_clause = update_sql.split("WHERE")[0]
    assert "enabled" not in set_clause
    assert "user_id" not in set_clause
    assert "role_id" not in set_clause
    delete_part = sql.split("UPDATE ai_agent_resource_permissions")[0]
    update_part = sql.split("UPDATE ai_agent_resource_permissions")[1]
    for part in (delete_part, update_part):
        assert "resource_type = 'element'" in part
        assert "resource_id LIKE 'menu:%'" in part, "必须限定只处理 menu: 前缀，避免误改功能点"
    # 不允许任何清表 / 删表动作。
    assert "TRUNCATE" not in sql.upper()
    assert "DROP " not in sql.upper()


def test_migration_is_idempotent_by_construction():
    """重复执行天然幂等：条件依赖 element + menu:%，执行后这些行已变成 menu，
    第二次 DELETE / UPDATE 都匹配 0 行，不会重复改或报错。"""
    for path in (MYSQL, PG):
        sql = _sql_only(path)
        assert "resource_type = 'element'" in sql.split("UPDATE")[1], (
            "UPDATE 必须靠 resource_type='element' 收敛：执行后不再匹配，因此可重复执行"
        )


def test_mysql_does_not_use_self_referencing_subquery():
    sql = _sql_only(MYSQL)
    # MySQL 不允许 UPDATE/DELETE 的子查询里引用同一张目标表（错误码 1093），
    # 且 apply_sql.py 的容错白名单不含 1093 → 迁移会直接失败。
    assert "IN (SELECT" not in sql.upper()


def test_pg_uses_is_not_distinct_from_and_native_syntax():
    sql = _sql_only(PG)
    assert "USING ai_agent_resource_permissions keep" in sql
    assert "IS NOT DISTINCT FROM" in sql, "PG 不能用 MySQL 的 <=>"
    assert "<=>" not in sql
    assert "`" not in sql, "PG 迁移不能出现 MySQL 反引号"
    assert "SET resource_type = 'menu'" in sql
    assert "keep.enabled = dup.enabled" in sql
    update_part = sql.split("UPDATE ai_agent_resource_permissions")[1]
    assert "resource_type = 'element'" in update_part
    assert "resource_id LIKE 'menu:%'" in update_part
    assert "TRUNCATE" not in sql.upper()
    assert "DROP " not in sql.upper()
