"""部门领域模型契约：表结构、唯一键、以及"明确不加外键"的约定。

用户决策（2026-10-20）：部门两张表**不建外键**，与 `ai_agent_user_role_relations`
（V27 起无 FK）保持一致，悬挂数据靠应用层清理 + 巡检兜底。

这条决策必须被测试锁住：后人"顺手"加个 `ForeignKey(...)` 或迁移里加
`FOREIGN KEY`，就会与既定约定冲突（并且 MySQL 侧历史上因缺 FK 踩过悬挂关系的坑，
见 `test_role_member_orphan_consistency.py`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint

from app.models.department import Department, UserDepartment

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MYSQL_MIGRATION = ROOT / "db-prod/V178-create_departments.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V79-create_departments.sql"


def test_department_table_shape():
    assert Department.__tablename__ == "ai_agent_departments"
    cols = {c.name for c in Department.__table__.columns}
    assert {
        "id",
        "parent_id",
        "code",
        "name",
        "path",
        "sort_order",
        "status",
        "description",
        "created_at",
        "updated_at",
    } <= cols


def test_department_code_is_unique():
    cols = {c.name for c in Department.__table__.columns}
    assert cols >= {"code"}
    assert Department.__table__.columns["code"].unique is True
    assert Department.__table__.columns["code"].nullable is False


def test_department_columns_do_not_declare_foreign_keys():
    """不建外键是明确决策，不是遗漏。"""
    for col in Department.__table__.columns:
        assert not col.foreign_keys, f"{col.name} 不应声明外键"


def test_user_department_table_shape():
    assert UserDepartment.__tablename__ == "ai_agent_user_departments"
    cols = {c.name for c in UserDepartment.__table__.columns}
    assert {"id", "user_id", "dept_id", "is_primary", "created_at"} <= cols


def test_user_department_unique_per_pair():
    uniques = [
        c for c in UserDepartment.__table__.constraints if isinstance(c, UniqueConstraint)
    ]
    assert any({col.name for col in c.columns} == {"user_id", "dept_id"} for c in uniques), (
        "同一用户在同一部门只能有一条关系，否则主部门判定会出现二义"
    )


def test_user_department_columns_do_not_declare_foreign_keys():
    for col in UserDepartment.__table__.columns:
        assert not col.foreign_keys, f"{col.name} 不应声明外键"


def test_migrations_create_both_tables_without_foreign_keys():
    for path in (MYSQL_MIGRATION, PG_MIGRATION):
        assert path.exists(), f"缺少迁移 {path}"
        sql = path.read_text(encoding="utf-8")
        assert "ai_agent_departments" in sql
        assert "ai_agent_user_departments" in sql
        assert "FOREIGN KEY" not in sql.upper()
        assert "REFERENCES" not in sql.upper()
