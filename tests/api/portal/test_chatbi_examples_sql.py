import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.api.portal.endpoints.examples import _chatbi_example_user_join
from app.models.chatbi_example import ChatBIExample
from app.models.user import User


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    ("db_type", "expected", "unexpected"),
    [
        # PostgreSQL：用户主键是 BIGINT，需要显式 cast 成字符串再比对
        ("postgresql", "CAST(ai_agent_users.id AS VARCHAR)", "concat(ai_agent_users.id"),
        # MySQL：用 concat 避开 utf8mb4_0900_ai_ci 与 utf8mb4_unicode_ci 的排序规则碰撞
        ("mysql", "concat(ai_agent_users.id", "CAST(ai_agent_users.id AS VARCHAR)"),
    ],
)
def test_chatbi_examples_user_join_casts_numeric_user_id_for_postgresql(
    monkeypatch, db_type, expected, unexpected
):
    """两个数据库分支都要按当前 DATABASE_TYPE 选择，而不是依赖运行环境的默认值。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_TYPE", db_type, raising=False)

    statement = select(ChatBIExample.id).select_from(ChatBIExample).outerjoin(
        User,
        _chatbi_example_user_join(),
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert expected in sql
    assert unexpected not in sql
    assert "ai_agent_users.id = ai_chatbi_examples.user_id" not in sql
