"""智能体系统级发布：申请单与操作事件。

两张表都刻意不建物理外键——与 ``skill_publications`` / ``mcp_server_shares`` 同一惯例，
避免删除用户或智能体时撞 RESTRICT 报错；清理由应用层负责（删智能体时连带删申请单与事件，
删用户时申请单上的 ``requested_by`` / ``reviewed_by`` 置 NULL、事件上的 ``actor_id`` 不动）。

``ai_agents.is_system`` 仍然是运行时的唯一事实源；``AgentSystemRequest`` 只回答
「谁在什么时候申请把哪个智能体上架」，不参与路由与委派判定。
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects import mysql

from app.core.orm import Base


def _snapshot_text() -> Text:
    """快照可能很长（系统提示词 + 工具清单 + 技能配置）。

    MySQL 的 TEXT 只有 65,535 字节（utf8mb4 下约 2.1 万汉字），长提示词的快照会超；
    PG 的 TEXT 本身无上限，保持原样。每列单独构造，避免多列共享同一类型实例——
    写法与 ``app/models/audit.py`` 的历史长文本列一致。
    """
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class AgentSystemRequest(Base):
    """一次「申请发布为系统级」。

    状态流转：PENDING -> APPROVED / REJECTED / CANCELLED。
    只有 PENDING 会冻结智能体（所有者不能编辑），终态一律解冻。
    """

    __tablename__ = "ai_agent_system_requests"

    id = Column(String(36), primary_key=True)
    agent_id = Column(String(36), nullable=False, index=True)
    # SYSTEM_PUBLISH=申请发布为系统级；VERSION_RELEASE=申请把某版本发布上线。
    # 老环境若尚未补列，读出来会是 None，按 SYSTEM_PUBLISH 处理。
    request_type = Column(String(24), nullable=False, default="SYSTEM_PUBLISH", server_default="SYSTEM_PUBLISH")
    snapshot_version_id = Column(String(36), nullable=True)
    snapshot = Column(_snapshot_text(), nullable=True)
    status = Column(String(20), nullable=False, default="PENDING", index=True)
    request_comment = Column(Text, nullable=True)
    requested_by = Column(Integer, nullable=True)
    requested_at = Column(DateTime, default=datetime.now, nullable=False)
    reviewed_by = Column(Integer, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_comment = Column(Text, nullable=True)


class AgentSystemEvent(Base):
    """系统级生命周期的操作事件，发布历史抽屉的数据源。

    action 取值：``PUBLISH`` / ``UNPUBLISH`` / ``META_UPDATE`` / ``TOGGLE_ENABLED``。
    ``PUBLISH`` 存内容快照，``UNPUBLISH`` 存操作时受影响人数（与是否真的发了通知无关），
    两者都不改版本状态——版本状态仍由既有的「发布版本」流程维护。
    """

    __tablename__ = "ai_agent_system_events"

    id = Column(String(36), primary_key=True)
    agent_id = Column(String(36), nullable=False, index=True)
    action = Column(String(32), nullable=False)
    version_id = Column(String(36), nullable=True)
    version_number = Column(Integer, nullable=True)
    snapshot = Column(_snapshot_text(), nullable=True)
    reason = Column(Text, nullable=True)
    affected_user_count = Column(Integer, nullable=True)
    # 仅 UNPUBLISH 有值：那次下架到底有没有给用户发通知。
    # 重新上架时读它决定要不要发「已恢复」——上次没说「不能用了」，这次就别突然说「能用了」。
    notified = Column(Boolean, nullable=True)
    actor_id = Column(Integer, nullable=True)
    actor_name = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
