from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Date, Integer, JSON, String, Text, UniqueConstraint

from app.core.orm import Base


class KnowledgeBaseMetadata(Base):
    """平台侧知识库扩展元数据，RAGFlow Dataset 仍是检索事实源。"""

    __tablename__ = "knowledge_base_metadata"
    __table_args__ = (
        UniqueConstraint("ragflow_dataset_id", name="uix_kb_ragflow_dataset_id"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    ragflow_dataset_id = Column(String(64), nullable=False, index=True, comment="RAGFlow Dataset ID")
    name = Column(String(255), nullable=False, comment="知识库名称快照")
    description = Column(Text, nullable=True, comment="知识库描述快照")
    owner = Column(String(100), nullable=True, comment="业务归属/负责人")
    visibility = Column(String(32), default="private", comment="可见性: private/team/public")
    tags = Column(JSON, nullable=True, comment="平台侧标签")
    notes = Column(Text, nullable=True, comment="平台侧备注")
    extra_config = Column(JSON, nullable=True, comment="扩展配置")
    status = Column(String(32), default="active", index=True, comment="状态: active/deleted/missing")
    created_by = Column(String(64), nullable=True, comment="创建人")
    updated_by = Column(String(64), nullable=True, comment="更新人")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeBaseMetric(Base):
    """知识库与文档的每日运营统计指标。"""

    __tablename__ = "knowledge_base_metrics"
    __table_args__ = (
        UniqueConstraint("metric_date", "target_type", "target_id", name="uix_kb_metric_date_target"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    metric_date = Column(Date, nullable=False, index=True, comment="统计日期")
    target_type = Column(String(32), nullable=False, index=True, comment="统计类型: dataset/document")
    target_id = Column(String(64), nullable=False, index=True, comment="目标ID (dataset_id 或 doc_id)")
    target_name = Column(String(255), nullable=True, comment="名称快照")
    citation_count = Column(Integer, default=0, nullable=False, comment="被引用次数")
    search_count = Column(Integer, default=0, nullable=False, comment="被搜索/召回次数")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

