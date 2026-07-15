from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, JSON, String, Text

from app.core.orm import Base


class PortalNotification(Base):
    __tablename__ = "portal_notifications"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    category = Column(String(32), nullable=False, default="saved_report")
    level = Column(String(16), nullable=False, default="info")
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=True)
    resource_type = Column(String(32), nullable=True)
    resource_id = Column(String(64), nullable=True)
    meta_info = Column("metadata", JSON, nullable=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("idx_portal_notifications_user_created", "user_id", "created_at"),
        Index("idx_portal_notifications_user_read", "user_id", "read_at"),
    )
