from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, BigInteger
from datetime import datetime
from app.core.orm import Base

class MetaChangelog(Base):
    """元数据变更日志表"""
    __tablename__ = "meta_changelog"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True, comment='主键ID')
    resource_type = Column(String(20), nullable=False, index=True, comment='资源类型: dataset/table/column/metric/relationship')
    resource_id = Column(String(270), nullable=False, index=True, comment='资源ID')
    operation = Column(String(20), nullable=False, index=True, comment='操作类型: create/update/delete')
    old_data = Column(JSON, nullable=True, comment='变更前数据')
    new_data = Column(JSON, nullable=True, comment='变更后数据')
    changed_fields = Column(JSON, nullable=True, comment='变更字段列表（仅update操作）')
    user_id = Column(Integer, nullable=True, index=True, comment='操作用户ID')
    user_name = Column(String(64), nullable=True, comment='操作用户名')
    reason = Column(Text, nullable=True, comment='变更原因')
    created_at = Column(DateTime, default=datetime.now, index=True, comment='创建时间')

    def __repr__(self):
        return f"<MetaChangelog(id={self.id}, resource_type={self.resource_type}, operation={self.operation})>"
