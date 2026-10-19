from sqlalchemy import Column, String, Boolean, DateTime, Text
from datetime import datetime
from app.core.orm import Base

class SysApiTool(Base):
    __tablename__ = "sys_api_tools"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    method = Column(String(20), nullable=False, default='GET')
    url_template = Column(Text, nullable=False)
    headers = Column(Text, nullable=True)  # Stored as JSON string
    parameter_schema = Column(Text, nullable=True)  # Stored as JSON string
    # 请求签名 v1：密钥只存密文，由 API 层加解密，响应只暴露 configured 状态
    signing_enabled = Column(Boolean, default=False, nullable=False)
    signing_key_id = Column(String(64), nullable=True, unique=True)
    signing_secret_encrypted = Column(Text, nullable=True)
    signing_algorithm = Column(String(32), nullable=False, default="hmac-sha256")
    
    is_active = Column(Boolean, default=True)
    # 业务分组名：智能体配置的「工具能力」步骤按它归组；NULL 表示未分组（走关键字回落）
    group_name = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def signing_secret_configured(self) -> bool:
        """只告诉界面是否已配置，不泄露密钥本身。"""
        return bool(self.signing_secret_encrypted)
