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
    
    is_active = Column(Boolean, default=True)
    # 业务分组名：智能体配置的「工具能力」步骤按它归组；NULL 表示未分组（走关键字回落）
    group_name = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
