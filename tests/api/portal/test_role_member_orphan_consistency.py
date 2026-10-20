"""角色成员口径一致性：用户数必须等于打开后能看到的成员数。

线上现象（用户截图）：`miniprogram_user` 角色列表显示「1」，点开抽屉却是「共 0 位成员」。

根因是两处口径不同：

- `GET /roles` 的 `user_count` 只 `count(*) FROM ai_agent_user_role_relations`；
- `GET /roles/{id}/users` 的成员列表 `JOIN ai_agent_users`。

关系表里有一条指向已删除用户的悬挂行（实测 role_id=563 的 user_id=24554 在用户表中不存在），
于是「1」与「0」同时成立。角色成员数应当只统计**真实存在的用户**，否则管理端会一直看到
一个点开就消失的数字。

这里同时锁住清理迁移：悬挂关系属于无主数据，要能被一次性删除。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.sql.selectable import Select

from app.api.portal.endpoints.roles import get_role_users, list_roles
from app.models.permission import Role, UserRoleRelation
from app.models.user import User

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
ROLES_PY = ROOT / "app/api/portal/endpoints/roles.py"
MYSQL_MIGRATION = ROOT / "db-prod/V177-clean_orphan_user_role_relations.sql"
PG_MIGRATION = ROOT / "db-prod-pg/V78-clean_orphan_user_role_relations.sql"


class _Result:
    def __init__(self, rows=(), scalar_value=None):
        self._rows = list(rows)
        self._scalar = scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _RecordingSession:
    def __init__(self, responses, role=None):
        self._responses = list(responses)
        self._role = role
        self.executed: list = []

    async def get(self, model, pk):
        return self._role

    async def execute(self, stmt):
        self.executed.append(stmt)
        if not self._responses:
            raise AssertionError(f"意料之外的额外查询：{stmt}")
        return self._responses.pop(0)


def _compiled(stmt) -> str:
    """按 MySQL 方言编译，只看 FROM/JOIN 结构，不执行。"""
    assert isinstance(stmt, Select), f"期望 SELECT，实际是 {type(stmt)}"
    return str(stmt.compile(dialect=mysql.dialect()))


async def test_role_user_count_does_not_count_orphan_relations():
    """`user_count` 的计数语句必须 join 用户表，不能只数关系行。"""
    role = Role(id=563, code="miniprogram_user", name="小程序用户")
    session = _RecordingSession([
        _Result([], scalar_value=1),     # 角色总数
        _Result([role]),                 # 当前页角色
        _Result([], scalar_value=0),     # 该角色的 user_count
    ])

    await list_roles(page=1, size=20, search=None, admin={"role": "admin"}, db=session)

    count_sql = _compiled(session.executed[-1])
    assert "ai_agent_user_role_relations" in count_sql
    assert "JOIN ai_agent_users" in count_sql, (
        "user_count 必须与成员列表同口径（join 用户表），否则悬挂关系会让数字虚高"
    )


async def test_role_member_ids_exclude_orphan_relations():
    """`user_ids` 是整集替换的依据，同样不能带悬挂 id，否则保存时会被写回。"""
    role = Role(id=563, code="miniprogram_user", name="小程序用户")
    session = _RecordingSession([
        _Result([1]),            # 当前成员 ID
        _Result([], scalar_value=1),
        _Result([User(id=1, user_name="alice", real_name="Alice", status=1)]),
        _Result([]),             # 角色名批量查询
    ], role=role)

    await get_role_users(
        role_id=563, page=1, size=50, search=None, admin={"role": "admin"}, db=session
    )

    id_sql = _compiled(session.executed[0])
    assert "JOIN ai_agent_users" in id_sql
    # 关系表本身仍要在 FROM 里（否则就成了从用户表反查全部用户）
    assert "ai_agent_user_role_relations" in id_sql
    assert UserRoleRelation.__tablename__ in id_sql


def test_cleanup_migration_deletes_only_orphans():
    """清理迁移只能删“用户已不存在”的关系，绝不能误删正常成员。"""
    assert MYSQL_MIGRATION.exists(), "缺少 MySQL 清理迁移"
    sql = MYSQL_MIGRATION.read_text(encoding="utf-8")

    assert "DELETE" in sql.upper()
    assert "ai_agent_user_role_relations" in sql
    assert "ai_agent_users" in sql
    # LEFT JOIN + IS NULL 是“只删悬挂”的判据；出现无条件 DELETE 即为危险迁移
    assert "IS NULL" in sql.upper()
    assert "LEFT JOIN" in sql.upper()
    # 不允许出现“不带 join 判据的整表删除”
    assert "DELETE FROM ai_agent_user_role_relations;" not in sql.lower()


def test_pg_cleanup_migration_uses_not_exists():
    """PostgreSQL 侧同义实现（PG 不支持 MySQL 的多表 DELETE 语法）。"""
    assert PG_MIGRATION.exists(), "缺少 PostgreSQL 清理迁移"
    sql = PG_MIGRATION.read_text(encoding="utf-8").upper()

    assert "DELETE FROM" in sql
    assert "NOT EXISTS" in sql
    assert "AI_AGENT_USER_ROLE_RELATIONS" in sql
    assert "AI_AGENT_USERS" in sql


def test_delete_user_explicitly_removes_role_relations():
    """删除用户不能只依赖 ORM 关系集合，要显式按 user_id 删一次关系行。

    MySQL 侧该表没有外键（见 V27），一旦关系集合没被完整加载，漏删就会留下悬挂行——
    正是本次「角色列表 1 人、点开 0 人」的来源。
    """
    source = (ROOT / "app/api/portal/endpoints/management.py").read_text(encoding="utf-8")
    segment = source.split("async def delete_user(", 1)[1].split("@router.post(", 1)[0]

    assert "user.roles = []" in segment, "ORM 关系清空要保持"
    assert "delete(UserRoleRelation).where(UserRoleRelation.user_id == user_id)" in segment, (
        "删除用户时必须显式兜底删除角色关系，不能只靠 ORM 集合"
    )
