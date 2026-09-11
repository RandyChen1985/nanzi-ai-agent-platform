from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.orm import Base

class MetaDataset(Base):
    __tablename__ = "meta_datasets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, comment='数据集名称')
    display_name = Column(String(100), comment='显示名称')
    description = Column(Text, comment='描述')
    tags = Column(JSON, comment='标签列表')
    data_source = Column(String(50), default="clickhouse", comment="数据源类型: clickhouse, mysql")
    status = Column(Integer, default=0, comment="1:启用, 0:禁用")
    
    # 精细化数据权限配置
    enable_data_perm = Column(Boolean, default=False, comment='是否启用精细化数据权限校验')
    row_filter_config = Column(JSON, nullable=True, comment='行级权限配置策略')
    
    # RAGFlow Integration Fields
    rag_dataset_id = Column(String(64), nullable=True, comment='RAGFlow 侧对应的 Dataset ID')
    rag_synced_at = Column(DateTime, nullable=True, comment='最后同步到 RAGFlow 的时间')
    rag_sync_status = Column(Integer, default=0, comment='同步状态 (0:未同步, 1:同步中, 2:已同步, -1:失败)')
    rag_sync_notes = Column(Text, nullable=True, comment='同步日志/错误信息')
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    tables = relationship("MetaTable", back_populates="dataset", cascade="all, delete-orphan")
    metrics = relationship("MetaMetric", back_populates="dataset", cascade="all, delete-orphan")
    drift_alerts = relationship("MetaSchemaDriftAlert", back_populates="dataset", cascade="all, delete-orphan")


class MetaTable(Base):
    __tablename__ = "meta_tables"
    __table_args__ = (
        UniqueConstraint('dataset_id', 'physical_name', name='uix_dataset_physical_name'),
    )

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("meta_datasets.id", ondelete="CASCADE"), nullable=False)
    physical_name = Column(String(255), nullable=False, comment='物理表名')
    term = Column(String(255), nullable=False, comment='业务术语')
    description = Column(Text, comment='描述')
    synonyms = Column(JSON, comment='同义词列表')
    status = Column(Integer, default=1, comment='1:启用, 0:禁用')
    
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    dataset = relationship("MetaDataset", back_populates="tables")
    columns = relationship("MetaColumn", back_populates="table", cascade="all, delete-orphan")


class MetaColumn(Base):
    __tablename__ = "meta_columns"

    id = Column(Integer, primary_key=True, index=True)
    table_id = Column(Integer, ForeignKey("meta_tables.id", ondelete="CASCADE"), nullable=False)
    physical_name = Column(String(255), nullable=False, comment='物理字段名')
    term = Column(String(255), nullable=False, comment='业务术语')
    type = Column(String(50), comment='字段类型')
    description = Column(Text, comment='字段描述')
    enums = Column(JSON, comment='枚举值定义')
    synonyms = Column(JSON, comment='同义词列表')
    examples = Column(JSON, comment='示例值')
    foreign_key = Column(String(255), comment='外键关联')
    is_primary = Column(Integer, default=0, comment='是否主键')
    
    created_at = Column(DateTime, default=datetime.now)

    table = relationship("MetaTable", back_populates="columns")


class MetaMetric(Base):
    __tablename__ = "meta_metrics"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("meta_datasets.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False, comment='指标名称')
    display_name = Column(String(100), nullable=False, comment='显示名称')
    description = Column(Text, comment='业务口径描述')
    calculation_logic = Column(Text, comment='计算逻辑')
    unit = Column(String(20), comment='单位')
    tags = Column(JSON, comment='标签列表')

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    dataset = relationship("MetaDataset", back_populates="metrics")


class MetaRelationship(Base):
    __tablename__ = "meta_relationships"

    id = Column(Integer, primary_key=True, index=True)
    source_table_id = Column(Integer, ForeignKey("meta_tables.id", ondelete="CASCADE"), nullable=False)
    target_table_id = Column(Integer, ForeignKey("meta_tables.id", ondelete="CASCADE"), nullable=False)
    join_condition = Column(String(255), nullable=False, comment='关联条件')
    join_type = Column(String(20), default='LEFT', comment='关联类型')
    description = Column(Text, comment='关系描述')

    source_table = relationship("MetaTable", foreign_keys=[source_table_id])
    target_table = relationship("MetaTable", foreign_keys=[target_table_id])


class MetaSchemaDriftAlert(Base):
    """元数据 Schema 漂移异常告警与人机协同处置模型。

    记录来自运行时物理报错反哺与手动/自动巡检发现的字段级差异。
    """
    __tablename__ = "meta_schema_drift_alerts"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("meta_datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    table_id = Column(Integer, nullable=True)
    table_name = Column(String(255), nullable=False)
    column_name = Column(String(255), nullable=False)
    drift_type = Column(String(32), default="missing_in_db", comment="missing_in_db, new_in_db, type_mismatch")
    source = Column(String(32), default="runtime", comment="runtime, manual_inspection, cron_inspection")
    error_sample = Column(Text, nullable=True)
    hit_count = Column(Integer, default=1)
    status = Column(Integer, default=0, comment="0: pending, 1: resolved, 2: ignored")

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    dataset = relationship("MetaDataset", back_populates="drift_alerts")

