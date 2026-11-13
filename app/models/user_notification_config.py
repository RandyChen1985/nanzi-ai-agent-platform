from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, BigInteger
from datetime import datetime
from app.core.orm import Base

class UserNotificationConfig(Base):
    """用户个人通知渠道配置。

    一条记录 = 一个「群」（钉钉/飞书/企微）或一套渠道配置（email）。
    多群改造后同一 (user_id, channel_type) 可以有多条记录，
    由 ``is_default`` 标出该渠道的默认群，``name`` 供按名称指定发送目标。

    群 ID 就是主键 ``id``：全表唯一且删除后不复用，
    因此跨渠道校验（拿钉钉的 ID 去发飞书）与按 ID 指定都能靠它完成。
    """
    __tablename__ = "user_notification_configs"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    channel_type = Column(String(50), nullable=False) # dingtalk, wechat_work, feishu, email
    name = Column(String(64), nullable=False, default="") # 群名称，按名称指定发送目标时使用
    config_json = Column(Text, nullable=False) # JSON parameters as string
    is_default = Column(Boolean, nullable=False, default=False) # 该渠道的默认群

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
