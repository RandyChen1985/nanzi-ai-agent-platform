"""删除用户时的发布谱系处理。

发布表的两列用户引用（``skill_publications.source_user_id``、
``skill_publication_versions.submitted_by``）此前是 NOT NULL 外键，管理员删除
「发布过技能的用户」会撞 RESTRICT 直接 500。处理口径与「下架」一致：
已发布的平台技能真的从公共库消失，但发布记录、审核轨迹与 ``platform_skill_id``
保留，只把作者字段置 NULL。
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
MANAGEMENT = ROOT / "app/api/portal/endpoints/management.py"
MODEL = ROOT / "app/models/skill_publication.py"
MYSQL = ROOT / "db-prod" / "V180-nullable_skill_publication_authors.sql"
PG = ROOT / "db-prod-pg" / "V81-nullable_skill_publication_authors.sql"


def _delete_user_body() -> str:
    source = MANAGEMENT.read_text(encoding="utf-8")
    body = source.split("async def delete_user(", 1)[1]
    return body.split("\n@router.", 1)[0]


def test_delete_user_detaches_publication_authors_before_deleting_user():
    body = _delete_user_body()
    assert "SkillPublication" in body
    assert "source_user_id = None" in body
    assert body.index("SkillPublication") < body.index("await db.delete(user)")


def test_delete_user_unpublishes_published_platform_skill():
    body = _delete_user_body()
    # 已发布的平台技能必须真的从公共库消失，否则运行时照样能扫到
    assert "remove_materialized_skill" in body
    assert '"UNPUBLISHED"' in body
    assert '"REVOKED"' in body


def test_delete_user_withdraws_pending_versions():
    body = _delete_user_body()
    assert '"WITHDRAWN"' in body
    assert "cleanup_publication_snapshot" in body


def test_delete_user_explicitly_unbinds_review_and_revoke_trails():
    body = _delete_user_body()
    for column in ("submitted_by", "reviewed_by", "withdrawn_by", "revoked_by"):
        assert column in body, f"delete_user 必须解绑 {column}"
        assert f"{column}=None" in body.replace(" ", ""), f"{column} 必须置 NULL"


def test_publication_model_columns_are_nullable():
    source = MODEL.read_text(encoding="utf-8")
    assert "source_user_id = Column(BigInteger, ForeignKey(\"ai_agent_users.id\"), nullable=True)" in source
    assert "submitted_by = Column(BigInteger, ForeignKey(\"ai_agent_users.id\"), nullable=True)" in source


def test_both_migration_files_exist():
    assert MYSQL.is_file(), f"缺少 MySQL 迁移: {MYSQL}"
    assert PG.is_file(), f"缺少 PostgreSQL 迁移: {PG}"


def test_mysql_migration_drops_not_null():
    sql = MYSQL.read_text(encoding="utf-8")
    assert "MODIFY source_user_id BIGINT NULL" in sql
    assert "MODIFY submitted_by BIGINT NULL" in sql


def test_pg_migration_drops_not_null():
    sql = PG.read_text(encoding="utf-8")
    assert "ALTER COLUMN source_user_id DROP NOT NULL" in sql
    assert "ALTER COLUMN submitted_by DROP NOT NULL" in sql
