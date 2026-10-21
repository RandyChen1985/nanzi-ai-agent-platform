"""个人 MCP 分享记录。

与技能分享（``skill_shares``）同一套语义：分享即打快照，接收人接受后得到一份独立副本；
终态（接受 / 拒绝 / 撤回）清空 payload，不再占用数据库空间。

与 ``skill_shares`` 一样**不建物理外键**：删除用户时由 ``management.delete_user``
显式清理两个方向的记录（建了 RESTRICT 外键会让删用户直接 500）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text

from app.core.orm import Base


class McpServerShare(Base):
    __tablename__ = "mcp_server_shares"

    id = Column(String(36), primary_key=True)
    source_user_id = Column(BigInteger, nullable=False)
    source_server_id = Column(String(36), nullable=False)
    # 分享瞬间的服务名/备注/地址快照：列表展示不依赖源服务是否还在
    server_name = Column(String(100), nullable=False)
    remark = Column(String(500), nullable=True)
    sse_url = Column(Text, nullable=False)
    # 完整配置 + 工具清单的 JSON 快照；含凭证时里面是已加密字段，明文不落库
    payload = Column(Text, nullable=True)
    include_credentials = Column(Boolean, nullable=False, default=True)
    target_user_id = Column(BigInteger, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING")
    message = Column(String(500), nullable=True)
    accepted_server_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    responded_at = Column(DateTime, nullable=True)
