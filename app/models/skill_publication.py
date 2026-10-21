from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.core.orm import Base


class SkillPublication(Base):
    __tablename__ = "skill_publications"

    id = Column(String(36), primary_key=True)
    platform_skill_id = Column(String(128), nullable=True, unique=True)
    # 可空：作者被删除时把行保留下来（审核审计与 platform_skill_id 不丢），只解绑作者
    source_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=True)
    source_personal_skill_id = Column(String(128), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    current_version = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="PENDING")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=True)

    versions = relationship(
        "SkillPublicationVersion",
        back_populates="publication",
        cascade="all, delete-orphan",
        order_by="SkillPublicationVersion.version_number",
    )

    __table_args__ = (
        Index("idx_skill_publications_source", "source_user_id", "source_personal_skill_id"),
        Index("idx_skill_publications_status", "status"),
    )


class SkillPublicationVersion(Base):
    __tablename__ = "skill_publication_versions"

    id = Column(String(36), primary_key=True)
    publication_id = Column(
        String(36),
        ForeignKey("skill_publications.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    snapshot_path = Column(String(1024), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    file_count = Column(Integer, nullable=False, default=0)
    total_size = Column(BigInteger, nullable=False, default=0)
    # 可空：同 source_user_id，提交人被删除后保留版本记录
    submitted_by = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=True)
    submitted_at = Column(DateTime, default=datetime.now, nullable=False)
    reviewed_by = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_comment = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    materialized_path = Column(String(1024), nullable=True)
    withdrawn_by = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=True)
    withdrawn_at = Column(DateTime, nullable=True)

    publication = relationship("SkillPublication", back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "version_number",
            name="ux_skill_publication_version_number",
        ),
        Index("idx_skill_publication_versions_status", "status"),
        Index("idx_skill_publication_versions_publication", "publication_id", "status"),
    )
