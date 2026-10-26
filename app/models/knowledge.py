from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Date, Float, Index, Integer, JSON, String, Text, UniqueConstraint

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



class KnowledgeAuditLog(Base):
    """知识库业务审计：谁在什么时候对这个库做了什么。

    刻意独立于 ``ai_agent_access_logs``：那张表是全站 API 访问日志（每个请求一条，
    默认只查 90 天，通常还会定期清理），而业务审计要求「谁把库转交出去了」这类记录
    长期可查。``dataset_id`` 也必须是一等列 —— 埋在访问日志的 ``request_params``
    JSON 文本里就只能全表 LIKE，库一多就查不动。
    """

    __tablename__ = "ai_agent_knowledge_audit_logs"
    __table_args__ = (
        # 详情页「操作日志」tab 的主查询路径：按库倒序翻页
        Index("idx_kal_dataset_created", "dataset_id", "created_at"),
        Index("idx_kal_action", "action", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    dataset_id = Column(
        String(64),
        nullable=False,
        index=True,
        comment="知识库（RAGFlow Dataset）ID；库被删除后日志保留",
    )
    action = Column(
        String(64),
        nullable=False,
        comment="操作码：datasets:create / documents:upload / datasets:grant ...",
    )
    # 执行人。user_id 用于改名后仍能追溯，user_name 是当时的快照。
    user_id = Column(BigInteger, nullable=True, comment="操作人用户 ID")
    user_name = Column(String(64), nullable=True, index=True, comment="操作人账号快照")
    status_code = Column(Integer, nullable=False, default=200, comment="HTTP 状态码（>=400 即失败）")
    # 操作细节：文件名、文档数、授权对象、变更前后……各 action 结构不同，统一存 JSON
    detail = Column(JSON, nullable=True, comment="操作细节（文件名/文档数/授权对象/变更前后）")
    client_ip = Column(String(50), nullable=True, comment="来源 IP")
    error_message = Column(Text, nullable=True, comment="失败原因")
    created_at = Column(DateTime, default=datetime.now, index=True, comment="操作时间")


class KnowledgeAccessRequest(Base):
    """知识库访问申请：未授权用户申请 → 库归属人/管理员审批 → 落授权记录。

    审批通过只是写一条 ``ResourcePermission``（与手工授权完全等价），
    所以后续撤回、审计、归属人转移都沿用既有链路，不需要第二套权限体系。
    """

    __tablename__ = "ai_agent_knowledge_access_requests"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    dataset_id = Column(String(64), nullable=False, index=True, comment="知识库（RAGFlow Dataset）ID")
    dataset_name = Column(String(255), nullable=True, comment="提交时的库名快照")
    applicant_user_id = Column(BigInteger, nullable=False, index=True, comment="申请人用户 ID")
    applicant_user_name = Column(String(64), nullable=True, comment="申请人账号快照")
    applicant_dept_id = Column(BigInteger, nullable=True, comment="申请人所属部门")
    reason = Column(Text, nullable=True, comment="申请理由")
    # pending / approved / rejected / cancelled
    status = Column(String(16), nullable=False, default="pending", index=True, comment="审批状态")
    reviewer_user_id = Column(BigInteger, nullable=True, comment="审批人用户 ID")
    reviewer_user_name = Column(String(64), nullable=True, comment="审批人账号快照")
    review_comment = Column(Text, nullable=True, comment="审批意见")
    reviewed_at = Column(DateTime, nullable=True, comment="审批时间")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class KnowledgeRetrievalLog(Base):
    """一次检索的结果记录：用来回答「哪些问题搜不到」。

    与 ``KnowledgeAuditLog`` 刻意分开：审计记管理动作、长期保留；
    这张表按时间聚合并可定期清理，混在一起会让聚合查询扫全表。
    """

    __tablename__ = "ai_agent_knowledge_retrieval_logs"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    query = Column(String(512), nullable=False, comment="检索词")
    dataset_ids = Column(JSON, nullable=True, comment="涉及的知识库 ID 列表")
    hit_count = Column(Integer, nullable=False, default=0, comment="命中切片数，0 即零结果")
    top_score = Column(Float, nullable=True, comment="最高相似度")
    latency_ms = Column(Integer, nullable=True, comment="检索耗时（毫秒）")
    source = Column(String(32), nullable=False, default="test", comment="test / agent")
    user_id = Column(BigInteger, nullable=True, comment="发起人用户 ID")
    user_name = Column(String(64), nullable=True, comment="发起人账号快照")
    created_at = Column(DateTime, default=datetime.now, index=True)


class KnowledgeHealthSnapshot(Base):
    """一次巡检里某个库的健康快照。

    按天落表而不是实时算：实时算要对每个库调一次文档列表，库一多就是
    一串慢请求，而且每次打开页面都要重算。
    """

    __tablename__ = "ai_agent_knowledge_health_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_date", "dataset_id", name="uix_khs_date_dataset"),
    )

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, index=True, comment="巡检日期")
    dataset_id = Column(String(64), nullable=False, index=True, comment="知识库 ID")
    name = Column(String(255), nullable=True, comment="巡检时的库名快照")
    documents = Column(Integer, nullable=False, default=0)
    parsed = Column(Integer, nullable=False, default=0, comment="已解析完成")
    parsing = Column(Integer, nullable=False, default=0, comment="解析中")
    failed = Column(Integer, nullable=False, default=0, comment="解析失败")
    unparsed = Column(Integer, nullable=False, default=0, comment="尚未解析")
    chunks = Column(Integer, nullable=False, default=0, comment="切片总数")
    is_missing = Column(Boolean, nullable=False, default=False, comment="引擎侧不存在")
    visibility = Column(String(32), nullable=True, comment="可见范围快照")
    owner_user_id = Column(BigInteger, nullable=True, comment="归属人快照")
    stale_days = Column(Integer, nullable=True, comment="距最近一次更新的天数")
    created_at = Column(DateTime, default=datetime.now)
