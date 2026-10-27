from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
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
    
    # 资产质量治理分（巡检结算时写入，0-100）
    quality_score = Column(Integer, nullable=True, comment='数据资产质量治理分 0-100')
    quality_breakdown = Column(JSON, nullable=True, comment='质量分维度明细')
    quality_scored_at = Column(DateTime, nullable=True, comment='最近一次质量评分时间')
    
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
    partition_fields = Column(JSON, nullable=True, comment='分区字段物理名列表')
    index_fields = Column(JSON, nullable=True, comment='索引字段物理名列表')
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
    dimension_role = Column(String(20), default="none", comment='维度角色: none/time/geo/category/identifier')
    hierarchy_group = Column(String(100), nullable=True, comment='层级组标识，同组字段构成下钻链')
    hierarchy_order = Column(Integer, nullable=True, comment='组内层级序号，从小到大=从粗到细')
    
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
    drift_type = Column(String(32), default="missing_in_db", comment="missing_in_db, new_in_db, type_mismatch, table_missing_in_db, missing_comment")
    source = Column(String(32), default="runtime", comment="runtime, manual_inspection, cron_inspection")
    error_sample = Column(Text, nullable=True)
    hit_count = Column(Integer, default=1)
    status = Column(Integer, default=0, comment="0: pending, 1: resolved, 2: ignored")

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    dataset = relationship("MetaDataset", back_populates="drift_alerts")


class MetadataAuditLog(Base):
    """数据集业务审计：谁在什么时候对哪个数据集做了什么。

    与 ``KnowledgeAuditLog`` 对称，独立于全站访问日志：访问日志每请求一条、
    默认只查 90 天且会被定期清理，``resource_id`` 还埋在 ``request_params`` 的
    JSON 文本里，按数据集查只能全表 LIKE；而 ``ResourcePermission`` 只有当前
    状态没有历史，「这个数据集是谁授权给谁的」事后查不出来。
    """

    __tablename__ = "ai_agent_metadata_audit_logs"
    __table_args__ = (
        Index("idx_mal_dataset_created", "metadata_dataset_id", "created_at"),
        Index("idx_mal_action_created", "action", "created_at"),
        Index("idx_mal_user", "user_id"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    metadata_dataset_id = Column(BigInteger, nullable=False, comment="meta_datasets.id")
    action = Column(String(64), nullable=False, comment="操作码: metadata:grant / metadata:reject ...")
    user_id = Column(BigInteger, nullable=True, comment="操作人用户 ID")
    user_name = Column(String(64), nullable=True, comment="操作人账号快照")
    status_code = Column(Integer, nullable=False, default=200, comment="HTTP 状态码（>=400 即失败）")
    detail = Column(JSON, nullable=True, comment="操作细节（申请ID/目标用户/审批意见）")
    client_ip = Column(String(50), nullable=True, comment="来源 IP")
    error_message = Column(Text, nullable=True, comment="失败原因")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="操作时间")
