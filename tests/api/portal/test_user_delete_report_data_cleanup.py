"""删除用户时清理「报表运行记录 / 门户通知」的契约。

两张表的 ``user_id`` 外键在真实迁移里**没有** ``ON DELETE CASCADE``：
``portal_saved_report_runs``（MySQL V96 与 PG V0-baseline 都缺）、
``portal_notifications``（PG V0-baseline 缺，MySQL V97 有）。用户被这两张表引用时，
``DELETE FROM ai_agent_users`` 会直接撞外键 → 500。runs 还可能指向别人分享给该用户的
报表（``report_id`` 属于他人），不会随 owner 级联删除，所以必须按 ``user_id`` 显式清。
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MANAGEMENT = ROOT / "app/api/portal/endpoints/management.py"
MYSQL_RUNS = ROOT / "db-prod" / "V96-create-portal-saved-report-runs.sql"
PG_BASELINE = ROOT / "db-prod-pg" / "V0-baseline.sql"


def _delete_user_body() -> str:
    source = MANAGEMENT.read_text(encoding="utf-8")
    body = source.split("async def delete_user(", 1)[1]
    # 截到下一个路由装饰器，避免把后面函数的代码也算进来
    return body.split("\n@router.", 1)[0]


def test_delete_user_removes_runs_and_notifications_scoped_by_user_id():
    body = _delete_user_body()
    assert "delete(PortalSavedReportRun).where(PortalSavedReportRun.user_id == user_id)" in body
    assert "delete(PortalNotification).where(PortalNotification.user_id == user_id)" in body


def test_cleanup_runs_before_deleting_user_row():
    """子表清理必须早于主表删除，否则必然外键报错。"""
    body = _delete_user_body()
    delete_user_at = body.index("await db.delete(user)")
    assert body.index("PortalSavedReportRun") < delete_user_at
    assert body.index("PortalNotification") < delete_user_at


def test_cleanup_is_inside_the_same_transaction():
    """清理与删用户共用同一次 commit：任何一步失败都由同一个 session 回滚。"""
    body = _delete_user_body()
    commit_at = body.index("await db.commit()")
    assert body.index("PortalSavedReportRun") < commit_at
    assert body.index("PortalNotification") < commit_at
    # 清理语句与删用户之间不得插入新的 commit，否则会删一半
    between = body[body.index("PortalSavedReportRun"):body.index("await db.delete(user)")]
    assert "db.commit()" not in between


def test_cleanup_does_not_touch_other_users_rows():
    """只按 user_id 删，不得整表清空。"""
    body = _delete_user_body()
    # 整表删除的形态是 delete(X) 后面直接结束/右括号；带 .where(...) 的不算
    assert not re.search(r"delete\(PortalSavedReportRun\)\s*(?=[\n)])", body)
    assert not re.search(r"delete\(PortalNotification\)\s*(?=[\n)])", body)


def _constraint_block(sql: str, constraint: str) -> str:
    start = sql.index(constraint)
    tail = sql[start:]
    # 约束子句到下一个右括号 + 逗号/换行结束（MySQL 多行约束写法）
    end = re.search(r"\)\s*(,|\)|ENGINE)", tail)
    return tail[: end.end()] if end else tail


def test_mysql_runs_user_fk_really_has_no_cascade():
    """这条是「必须显式清理」的依据：MySQL 侧 runs.user_id 无级联。"""
    sql = MYSQL_RUNS.read_text(encoding="utf-8")
    block = _constraint_block(sql, "fk_portal_saved_report_runs_user")
    assert "ai_agent_users" in block
    assert "ON DELETE" not in block


def test_pg_notifications_and_runs_user_fk_have_no_cascade():
    """PG baseline：portal_notifications / portal_saved_report_runs 都缺级联。"""
    sql = PG_BASELINE.read_text(encoding="utf-8")
    for table in ("portal_notifications", "portal_saved_report_runs"):
        start = sql.index(f'CREATE TABLE IF NOT EXISTS "{table}"')
        block = sql[start : sql.index("CREATE TABLE IF NOT EXISTS", start + 10)]
        fk_lines = [
            line
            for line in block.splitlines()
            if "ai_agent_users" in line and "FOREIGN KEY" in line
        ]
        assert fk_lines, f"{table} 应有指向 ai_agent_users 的外键"
        assert all("CASCADE" not in line for line in fk_lines), (
            f"{table}.user_id 若已补级联，本清理仍应保留（跨库一致），"
            f"但迁移事实已变：{fk_lines}"
        )


# ---------------------------------------------------------------------------
# 运行时路径：真调 delete_user，断言发出的 SQL 顺序（不依赖真实数据库）
# ---------------------------------------------------------------------------

class _EmptyScalars:
    def all(self):
        return []

    def first(self):
        return None


class _EmptyResult:
    """delete_user 里所有 execute 都是「查不到行」的场景。"""

    def scalars(self):
        return _EmptyScalars()

    def scalar(self):
        return 0

    def scalar_one_or_none(self):
        return None


@pytest.mark.asyncio
async def test_delete_user_issues_child_cleanup_before_user_delete(monkeypatch):
    """按真实调用顺序钉住：子表 DELETE 必须先于 ``delete(user)`` 且同一事务提交。"""
    from unittest.mock import AsyncMock, MagicMock

    from app.api.portal.endpoints import management as management_endpoint
    from app.models.user import User
    from app.services.auth_service import AuthService
    from app.services.permission_service import PermissionService

    user = User(id=42, user_name="u42", role="user", api_key_hash="hash-42")
    events = []

    async def _execute(stmt, *args, **kwargs):
        events.append(("execute", str(stmt.compile(compile_kwargs={"literal_binds": True}))))
        return _EmptyResult()

    async def _delete(obj):
        events.append(("delete", type(obj).__name__))

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.get = AsyncMock(return_value=user)
    db.flush = AsyncMock()
    db.delete = AsyncMock(side_effect=_delete)
    db.commit = AsyncMock()

    # 收尾的缓存清理会碰 Redis，这里只验证删除链路本身
    monkeypatch.setattr(AuthService, "invalidate_user_auth_cache", AsyncMock())
    monkeypatch.setattr(
        PermissionService, "invalidate_cached_permissions_for_users", AsyncMock()
    )

    await management_endpoint.delete_user(user_id=42, admin={"user_id": 1}, db=db)

    runs_sql = "DELETE FROM portal_saved_report_runs WHERE portal_saved_report_runs.user_id = 42"
    notifications_sql = "DELETE FROM portal_notifications WHERE portal_notifications.user_id = 42"
    assert ("execute", runs_sql) in events, f"缺少 runs 清理：{events}"
    assert ("execute", notifications_sql) in events, f"缺少 notifications 清理：{events}"

    delete_user_at = next(i for i, e in enumerate(events) if e[0] == "delete" and e[1] == "User")
    assert events.index(("execute", runs_sql)) < delete_user_at
    assert events.index(("execute", notifications_sql)) < delete_user_at
    db.commit.assert_awaited_once()
