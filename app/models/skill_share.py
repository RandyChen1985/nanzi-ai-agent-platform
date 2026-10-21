from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.core.orm import Base


class SkillShare(Base):
    """个人技能点对点分享记录。

    一行 = 一次「分享人把某个个人技能分享给某个接收人」的邀请。`PENDING` 期间
    ``snapshot_path`` 指向一份不可变快照；接受/拒绝/撤回后快照立即回收，
    快照目录置空，但记录保留用于血缘与「我的分享」列表。
    """

    __tablename__ = "skill_shares"

    id = Column(String(36), primary_key=True)
    source_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False)
    source_skill_id = Column(String(128), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    message = Column(String(500), nullable=True)
    target_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    snapshot_path = Column(String(1024), nullable=True)
    content_sha256 = Column(String(64), nullable=False)
    file_count = Column(Integer, nullable=False, default=0)
    total_size = Column(BigInteger, nullable=False, default=0)
    accepted_skill_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    responded_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_skill_shares_source", "source_user_id", "source_skill_id", "status"),
        Index("idx_skill_shares_target", "target_user_id", "status", "created_at"),
    )
