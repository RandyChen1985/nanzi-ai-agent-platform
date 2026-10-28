"""会话在线分享表。

与 ``agent_share`` / ``skill_share`` 同一语义：分享即打快照，源会话后续被改、被删都不
影响已发出的分享。刻意不建物理外键——删除用户由管理侧统一清理，避免外键把会话删除
也一起卡住。

``content_json`` 用 LONGTEXT：长会话（数百轮）在 MySQL 严格模式下会把普通 TEXT
（65KB）整行打回。

索引与迁移脚本（``db-prod/V100-create-conversation-shares.sql``）保持同名同列，
避免「迁移建了一套、模型写着另一套」这种只在某个环境才暴露的漂移。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects import mysql

from app.core.orm import Base


def _share_long_text() -> Text:
    """MySQL 用 LONGTEXT（4GB），PostgreSQL 保持 TEXT（本身无上限）。"""
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class ConversationShare(Base):
    """一条会话分享记录：冻结正文 + 访问控制 + 打开计数。"""

    __tablename__ = "ai_conversation_shares"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    share_id = Column(String(32), nullable=False, unique=True, comment="对外链接 ID")
    conversation_id = Column(String(50), nullable=True, comment="被分享会话 ID（仅追溯）")
    owner_user_id = Column(String(64), nullable=False, comment="分享人用户 ID")
    owner_username = Column(String(64), nullable=True, comment="分享人用户名（展示用）")
    agent_id = Column(String(36), nullable=True, comment="分享时智能体 ID")
    agent_name = Column(String(128), nullable=True, comment="分享时智能体显示名")
    title = Column(String(500), nullable=True, comment="会话标题（首轮提问截断）")
    access_mode = Column(String(16), nullable=False, default="secret", comment="public / secret")
    secret_cipher = Column(String(512), nullable=True, comment="访问密码密文（Fernet）")
    content_json = Column(_share_long_text(), nullable=True, comment="冻结的消息快照")
    message_count = Column(Integer, nullable=False, default=0, comment="消息条数")
    turn_count = Column(Integer, nullable=False, default=0, comment="问答轮数")
    expires_at = Column(DateTime, nullable=True, comment="失效时间，NULL=永久")
    status = Column(String(16), nullable=False, default="active", comment="active / revoked")
    revoked_at = Column(DateTime, nullable=True, comment="作废时间")
    view_count = Column(Integer, nullable=False, default=0, comment="成功打开次数")
    last_viewed_at = Column(DateTime, nullable=True, comment="最近一次成功打开")
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment="创建时间")

    __table_args__ = (
        Index("idx_ai_conversation_shares_owner", "owner_user_id", "created_at"),
        Index("idx_ai_conversation_shares_conversation", "conversation_id"),
        Index("idx_ai_conversation_shares_created_at", "created_at"),
    )
