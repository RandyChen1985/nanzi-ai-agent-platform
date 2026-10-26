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
    # 展示用文本快照：可能是历史自由文本，也可能是写入时按部门名回填的快照。
    # 部门改名后以 owner_dept_id 关联出的最新部门名为准，这里只做兜底。
    owner = Column(String(100), nullable=True, comment="业务归属部门/负责人（文本快照）")
    # 业务归属部门。public/private 下只用于浏览分组与搜索；
    # team（部门级）下它同时是权限判定依据：归属部门（含下级部门）成员据此
    # 自动获得只读权（见 PermissionService.resolve_knowledge_dataset_access）。
    owner_dept_id = Column(
        BigInteger,
        nullable=True,
        index=True,
        comment="业务归属部门 ID（team 级下的自动只读依据）",
    )
    # 归属人（管理权唯一真源）：非空时判定只看它，created_by 文本不再参与 ——
    # 否则「把库转交给同事」之后，原创建人仍能凭文本快照保留管理权，转移等于没做。
    # 为空时回落 created_by 比较，兼容未回填与已离职的老库。
    owner_user_id = Column(
        BigInteger,
        nullable=True,
        index=True,
        comment="归属人用户ID（管理权判定依据；为空时回落 created_by 文本）",
    )
    visibility = Column(
        String(32),
        default="private",
        comment="可见范围: private(仅创建人，新建默认)/team(部门自动只读)/public(需授权)",
    )
    tags = Column(JSON, nullable=True, comment="平台侧标签")
    notes = Column(Text, nullable=True, comment="平台侧备注")
    extra_config = Column(JSON, nullable=True, comment="扩展配置")
    status = Column(String(32), default="active", index=True, comment="状态: active/deleted/missing")
    # 创建人用户名**快照**，仅用于展示与「转移前的兜底判定」：
    # 转移归属只改 owner_user_id，不改这里，避免真实的创建记录被覆盖掉。
    created_by = Column(String(64), nullable=True, comment="创建人用户名快照（展示用；判定见 owner_user_id）")
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

