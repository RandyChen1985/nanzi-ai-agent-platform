"""管理员站内信群发的发送记录。

为什么需要这张表（而不是只靠请求日志）：群发是「一次写入成百上千条、发出去收不回」
的广播动作，请求日志（``ai_agent_access_logs``）的 ``request_params`` 会被截断到 10KB，
也无法按「哪一次群发」聚合。本表只记输入与结果，用于管理页的历史列表与回填重发。

**只存范围，不存最终收件人名单**：名单可由范围重新解析；存快照会在人员变动后与事实
不符，重发时重新预览并把人数差异摆出来比"照旧名单再发一次"安全。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, JSON, String, Text

from app.core.orm import Base


class PortalBroadcastRecord(Base):
    __tablename__ = "portal_broadcast_records"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # 发送者快照：不建外键 —— 账号可能被停用或删除，历史记录仍需可读可查
    sender_user_id = Column(BigInteger, nullable=False)
    sender_user_name = Column(String(64), nullable=False, default="")
    sender_real_name = Column(String(64), nullable=True)
    # 与 portal_notifications.title VARCHAR(200) 同上限
    title = Column(String(200), nullable=False)
    # 正文原文快照（Markdown），重发时原样回填到表单
    content = Column(Text, nullable=False)
    level = Column(String(16), nullable=False, default="info")
    # 三个范围维度：与群发请求体同构，重发时可直接回填
    audience_user_ids = Column(JSON, nullable=True)
    audience_dept_ids = Column(JSON, nullable=True)
    audience_role_ids = Column(JSON, nullable=True)
    recipient_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
