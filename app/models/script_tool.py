from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects import mysql
from datetime import datetime

from app.core.orm import Base


class SysScriptTool(Base):
    """Python 脚本工具配置（与 ``sys_api_tools`` 并列的第二种自定义工具）。

    ``source='builtin'`` 的行只承载元数据与启停状态，源码唯一来源是
    ``app/services/ai/tools/script_tool_builtins.py``：改内置脚本不必再写迁移。
    """

    __tablename__ = "sys_script_tools"

    id = Column(String(36), primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    source = Column(String(16), nullable=False, default="custom")
    language = Column(String(16), nullable=False, default="python")
    # 与迁移的 MEDIUMTEXT 对齐：Text 在 MySQL 上只有 64KB，装不下 200KB 的脚本上限
    code = Column(Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True)
    parameter_schema = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)
    timeout_seconds = Column(Integer, nullable=True)
    group_name = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    @property
    def is_builtin(self) -> bool:
        return str(self.source or "").strip().lower() == "builtin"
