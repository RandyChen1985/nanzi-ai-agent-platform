"""删除用户时要连带清理技能分享。

`skill_shares` 与部门表同一惯例：数据库层不建物理外键，由 `delete_user` 显式
清理。漏了这一步不会在创建端暴露，只会在管理员删除「分享过 / 接收过技能」的
用户时变成 `Cannot delete or update a parent row` 的 500，而且 PENDING 分享的
快照目录会永远留在磁盘上。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MANAGEMENT = ROOT / "app/api/portal/endpoints/management.py"
MYSQL = ROOT / "db-prod" / "V179-create_skill_shares.sql"
PG = ROOT / "db-prod-pg" / "V80-create_skill_shares.sql"


def _delete_user_body() -> str:
    source = MANAGEMENT.read_text(encoding="utf-8")
    body = source.split("async def delete_user(", 1)[1]
    # 截到下一个路由装饰器，避免把后面函数的代码也算进来
    return body.split("\n@router.", 1)[0]


def test_delete_user_cleans_skill_shares_before_deleting_user():
    body = _delete_user_body()
    assert "SkillShare" in body, "delete_user 必须显式删除 skill_shares"
    assert "remove_share_snapshot" in body, "PENDING 分享的快照目录必须在删行前回收"
    # 顺序要求：先清理引用方，最后才能删 user 本体
    assert body.index("SkillShare") < body.index("await db.delete(user)")


def test_delete_user_covers_both_share_directions():
    body = _delete_user_body()
    assert "source_user_id" in body
    assert "target_user_id" in body


@pytest.mark.parametrize("path", [MYSQL, PG])
def test_share_migrations_skip_physical_foreign_keys(path: Path):
    """物理外键会让「删除用户」直接 500；与 ai_agent_user_departments 保持同一惯例。"""
    sql = path.read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in sql.splitlines() if not line.strip().startswith("--")
    )
    assert "REFERENCES ai_agent_users" not in executable
    assert "FOREIGN KEY" not in executable.upper()
