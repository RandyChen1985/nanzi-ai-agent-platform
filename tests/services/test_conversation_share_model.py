"""模型契约：表名、长文本变体、索引名必须与迁移脚本一致。"""

import pytest
from sqlalchemy.dialects import mysql

from app.models.conversation_share import ConversationShare

pytestmark = pytest.mark.no_infrastructure


def test_table_and_key_columns():
    assert ConversationShare.__tablename__ == "ai_conversation_shares"
    columns = ConversationShare.__table__.columns
    for name in (
        "share_id", "conversation_id", "owner_user_id", "access_mode",
        "secret_cipher", "content_json", "expires_at", "status",
        "view_count", "last_viewed_at",
    ):
        assert name in columns, name
    assert columns["share_id"].unique is True
    assert columns["expires_at"].nullable is True


def test_content_json_uses_longtext_on_mysql():
    content_type = ConversationShare.__table__.columns["content_json"].type
    assert content_type.dialect_impl(mysql.dialect()).__class__.__name__ == "LONGTEXT"


def test_indexes_declared():
    names = {index.name for index in ConversationShare.__table__.indexes}
    assert "idx_ai_conversation_shares_owner" in names
    assert "idx_ai_conversation_shares_conversation" in names
    # 列表按 created_at 倒序取，索引名与迁移脚本保持一致
    assert "idx_ai_conversation_shares_created_at" in names


def test_no_physical_foreign_keys():
    """刻意不建物理外键：删用户 / 删会话由管理侧统一清理，别让外键卡住正常删除。"""
    assert ConversationShare.__table__.foreign_keys == set()
