from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from datetime import datetime
from app.core.orm import Base


class MetaDbConnectionConfig(Base):
    """元数据导入 - 数据库连接配置（持久化到 DB，避免每次重复输入）"""
    __tablename__ = "meta_db_connection_configs"
    __table_args__ = (
        UniqueConstraint("name", name="uk_meta_db_connection_configs_name"),
    )

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(100), nullable=False, comment='连接别名，如"生产-业务库"')
    db_type       = Column(String(20), nullable=False, comment='数据库类型: mysql|clickhouse|oracle|sqlserver')
    host          = Column(String(255), nullable=False, comment='主机地址')
    port          = Column(Integer, nullable=False, comment='端口号')
    db_user       = Column(String(100), nullable=False, comment='数据库用户名')
    password      = Column(String(255), nullable=False, default='', comment='密码（明文）')
    database_name = Column(String(100), nullable=False, comment='数据库/库名')
    description   = Column(String(500), nullable=False, default='', comment='备注/用途说明')
    created_by    = Column(Integer, nullable=False, default=0, comment='创建者用户 ID')
    created_at    = Column(DateTime, default=datetime.now)
    updated_at    = Column(DateTime, default=datetime.now, onupdate=datetime.now)
