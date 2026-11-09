"""通用 AI 产物元信息模型。

平台所有 AI 生成/导出的可下载产物（Word/Excel、查询导出等）统一登记到
`ai_artifacts` 表，**数据库只存元信息**，文件内容落在对应用户的工作区目录
（agent_workspaces/{user_key}/...）。下载/续期/到期清理均以 DB 元信息为准。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String

from app.core.orm import Base


class AiArtifact(Base):
    __tablename__ = "ai_artifacts"

    # 对外 artifact_id，沿用 32 位 hex（与 download_url 兼容）。
    id = Column(String(32), primary_key=True)
    owner_user_id = Column(BigInteger, ForeignKey("ai_agent_users.id"), nullable=False, index=True)
    conversation_id = Column(String(128), nullable=True, index=True)
    # 子代理委派产生的产物：conversation_id 仍是子会话（审计），父会话归属单独记录，
    # 主会话的产物列表/消息角标据此聚合，避免要求主智能体二次发布。
    parent_conversation_id = Column(String(128), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    parent_trace_id = Column(String(64), nullable=True, index=True)
    # 产物类型：word | excel | export
    artifact_type = Column(String(32), nullable=False)
    filename = Column(String(255), nullable=False)
    mime_type = Column(String(128), nullable=True)
    size = Column(BigInteger, nullable=False, default=0)
    # 内容实际路径，位于用户工作区内（resolve 后必须是工作区根下的子路径）。
    storage_path = Column(String(1024), nullable=False)
    token_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("idx_ai_artifacts_owner_created", "owner_user_id", "created_at"),
    )