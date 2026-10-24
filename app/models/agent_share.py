"""智能体分享：把配置副本发给某个同事。

分享**不是**授权共用。所有者把自己的智能体分享给某个人，对方接受后拿到一个
**全新的、属于自己的**智能体，需要重新命名并走向导确认配置；复制时剔除接收方
无权使用的工具 / MCP / 技能。源与副本从此互不影响。

系统级智能体不提供分享：它的可用范围由管理员统一授权治理（下架要按授权表统计
受影响人数并通知），所有者若能自己往外发副本，等于绕开这套治理。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects import mysql

from app.core.orm import Base

STATUS_PENDING = "PENDING"
STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_REVOKED = "REVOKED"
# 分享人主动撤回（与 REVOKED 区分：REVOKED 是系统因源变系统级而作废）
STATUS_WITHDRAWN = "WITHDRAWN"

# 分享默认有效期：不设时效的话，半年后有人接受一份早就过时的配置，
# 双方都说不清「这到底是哪一版」。
DEFAULT_EXPIRE_DAYS = 7


def _snapshot_text() -> Text:
    """快照含系统提示词 + 工具清单 + 技能配置，可能超过 MySQL TEXT 的 65,535 字节。

    与 ``app/models/agent_system.py`` 同一处理：MySQL 用 LONGTEXT，PG 保持 TEXT。
    """
    return Text().with_variant(mysql.LONGTEXT(), "mysql")


class AgentShareRequest(Base):
    """一条「把某智能体分享给某人」的记录。

    状态流转：PENDING -> ACCEPTED / REJECTED / EXPIRED / REVOKED。
    只有 PENDING 且未过期才能被接受。
    """

    __tablename__ = "ai_agent_share_requests"

    id = Column(String(36), primary_key=True)
    agent_id = Column(String(36), nullable=False, index=True)

    # 冗余存一份源标识与名称：源智能体被删掉后，收件箱里仍然要能说清「这是谁分享的什么」
    agent_name = Column(String(255), nullable=True)
    agent_display_name = Column(String(255), nullable=True)

    owner_user_id = Column(Integer, nullable=True)
    owner_user_name = Column(String(255), nullable=True)
    recipient_user_id = Column(Integer, nullable=False, index=True)
    recipient_user_name = Column(String(255), nullable=True)

    # 分享那一刻的完整配置快照。用快照而不是接受时现读源，是为了让
    # 「源后来被改 / 被删 / 被提升为系统级」都不影响已经发出的分享。
    # 注意必须用 Column(...) 包起来：只写 _snapshot_text() 时 SQLAlchemy 不会建立
    # 列映射，属性会保留成 Text 类型对象本身，读出来 json.loads 直接报
    # "the JSON object must be str, bytes or bytearray, not Text"
    snapshot = Column(_snapshot_text(), nullable=True)
    snapshot_version = Column(Integer, nullable=False, default=1)

    status = Column(String(20), nullable=False, default=STATUS_PENDING)
    share_message = Column(String(500), nullable=True)
    share_reason = Column(String(500), nullable=True)

    # 接受后生成的副本智能体（REJECTED / EXPIRED / REVOKED 时为 NULL）
    new_agent_id = Column(String(36), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)
