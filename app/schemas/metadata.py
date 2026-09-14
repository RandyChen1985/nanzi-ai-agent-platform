from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime

# --- Table/Column Schemas (Defined first for nesting) ---

class ColumnSchema(BaseModel):
    physical_name: str
    term: Optional[str] = None
    type: Optional[str] = "String"
    description: Optional[str] = None
    enums: Optional[List[Dict[str, Any]]] = None # [{"value": 1, "label": "Active"}]
    synonyms: Optional[List[str]] = []
    is_primary: Optional[bool] = False # Added field for UI

class TableCreate(BaseModel):
    physical_name: str
    term: Optional[str] = None
    description: Optional[str] = None
    synonyms: Optional[List[str]] = []
    columns: List[ColumnSchema] = []

class TableResponse(TableCreate):
    id: int
    dataset_id: int
    model_config = ConfigDict(from_attributes=True)

# --- Metric Schemas ---

class MetricSchema(BaseModel):
    name: str 
    display_name: str
    description: Optional[str] = None
    calculation_logic: str = ""
    unit: Optional[str] = None
    tags: Optional[List[str]] = []

class MetricRecommendRequest(BaseModel):
    table_names: Optional[List[str]] = None
    user_prompt: Optional[str] = None

class MetricResponse(MetricSchema):
    id: int
    dataset_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

# --- Relationship Schemas ---

class RelationshipSchema(BaseModel):
    source_table_id: int
    target_table_id: int
    join_condition: str
    join_type: str = "LEFT"
    description: Optional[str] = None

class RelationshipRecommendRequest(BaseModel):
    table_names: Optional[List[str]] = None
    user_prompt: Optional[str] = None
    strategy: Literal["strict", "smart"] = "strict"

class RelationshipResponse(RelationshipSchema):
    id: int
    dataset_id: Optional[int] = None  # Logical grouping
    model_config = ConfigDict(from_attributes=True)

# --- Dataset Schemas ---

class DatasetBase(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = []
    data_source: Optional[str] = "clickhouse"
    status: Optional[int] = 0
    enable_data_perm: Optional[bool] = False
    row_filter_config: Optional[Dict[str, Any]] = None

class DatasetCreate(DatasetBase):
    pass

class DatasetUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    data_source: Optional[str] = None
    status: Optional[int] = None
    enable_data_perm: Optional[bool] = None
    row_filter_config: Optional[Dict[str, Any]] = None

class DatasetResponse(DatasetBase):
    id: int
    created_at: datetime
    updated_at: datetime

    # RAGFlow Status
    rag_dataset_id: Optional[str] = None
    rag_synced_at: Optional[datetime] = None
    rag_sync_status: Optional[int] = 0
    rag_sync_notes: Optional[str] = None

    table_count: int = 0
    metric_count: int = 0
    relationship_count: int = 0

    # 资产质量治理分（巡检结算时写入；未巡检过则为 None）
    quality_score: Optional[int] = None
    quality_breakdown: Optional[Dict[str, Any]] = None
    quality_scored_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DatasetOptionResponse(BaseModel):
    """会话资源等场景的轻量数据集选项（不含表/指标/关系统计）。"""

    id: int
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    data_source: Optional[str] = None
    status: Optional[int] = 1

    model_config = ConfigDict(from_attributes=True)

class DatasetDetailResponse(DatasetResponse):
    tables: Optional[List[TableResponse]] = []
    metrics: Optional[List[MetricResponse]] = []
    relationships: Optional[List[RelationshipResponse]] = []

# --- DB Import Schemas ---

class DBConnectionConfig(BaseModel):
    type: str # 'mysql', 'clickhouse', 'oracle', 'sqlserver', 'postgresql'
    host: str
    port: int
    user: str
    password: str
    database: str

class DDLRequest(BaseModel):
    config: DBConnectionConfig
    tables: List[str]


# --- Batch Delete Schemas ---

class BatchDeleteTablesRequest(BaseModel):
    table_names: List[str]


class BatchDeleteMetricsRequest(BaseModel):
    metric_ids: List[int]


class BatchDeleteRelationshipsRequest(BaseModel):
    relationship_ids: List[int]


# --- Schema Drift Alerts & Inspection Schemas ---

class MetaDriftAlertResponse(BaseModel):
    id: int
    dataset_id: int
    table_id: Optional[int] = None
    table_name: str
    column_name: str
    drift_type: str  # missing_in_db, new_in_db, type_mismatch
    source: str  # runtime, manual_inspection, cron_inspection
    error_sample: Optional[str] = None
    hit_count: int = 1
    status: int = 0  # 0: pending, 1: resolved, 2: ignored
    dataset_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


DriftResolutionAction = Literal["drop_column", "add_column", "sync_type", "ignore", "drop_table", "update_comment"]


class ResolveDriftAlertRequest(BaseModel):
    action: DriftResolutionAction
    # add_column 场景下，管理员确认（或修改）后的字段业务信息；
    # 缺省时沿用当前后端默认逻辑（物理注释 → 英文物理名兜底）。
    term: Optional[str] = None
    description: Optional[str] = None
    synonyms: Optional[List[str]] = None


class BatchResolveDriftAlertsRequest(BaseModel):
    action: DriftResolutionAction
    drift_type: Optional[str] = None  # missing_in_db, new_in_db, type_mismatch, table_missing_in_db, missing_comment
    alert_ids: Optional[List[int]] = None
    # 批量录入（add_column）时，是否自动为物理库无注释的字段调用 AI 补全中文名与描述（缺省 True）
    auto_ai: bool = True


class AnalyzeColumnRequest(BaseModel):
    """对单个物理新增字段发起 LLM 语义分析的请求体。"""

    # 可选：是否附带样例值辅助理解；缺省 True（读空表/失败时自动降级为仅名+类型）
    with_samples: bool = True


class AnalyzeColumnResponse(BaseModel):
    """单个物理新增字段的 AI 语义分析预览结果，供管理员确认/修改后再落库。"""

    alert_id: int
    dataset_id: int
    dataset_name: Optional[str] = None
    table_name: str
    column_name: str
    physical_type: Optional[str] = None
    comment: Optional[str] = None
    sample_values: List[Any] = Field(default_factory=list)
    term: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    llm_succeeded: bool = False
    ai_error: Optional[str] = None
    # 上下文（同表其它字段的中文术语），供前端展示提示，也可用于拼装提示词
    sibling_terms: List[str] = Field(default_factory=list)


class AnalyzeUpdateCommentResponse(BaseModel):
    """备注缺失字段（missing_comment 告警）的补充分析预览结果，供管理员确认/修改后再落库。

    策略：物理库已有有效备注时直接采用（from_source='physical'）；否则由 LLM 依据现有
    业务术语、类型与样例值推断中文业务描述（from_source='ai'）；均无效时为 'none'。
    """

    alert_id: int
    dataset_id: int
    dataset_name: Optional[str] = None
    table_name: str
    column_name: str
    physical_type: Optional[str] = None
    comment: Optional[str] = None
    sample_values: List[Any] = Field(default_factory=list)
    # 该字段现有的业务术语（保留，不在此流程改动）
    current_term: Optional[str] = None
    # 该字段当前备注
    current_description: Optional[str] = None
    # 建议的中文业务描述
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    # 结果来源：physical=物理库注释优先, ai=LLM 推断, none=均无有效结果
    from_source: Literal["physical", "ai", "none"] = "none"
    llm_succeeded: bool = False
    ai_error: Optional[str] = None
    # 上下文（同表其它字段的中文术语）
    sibling_terms: List[str] = Field(default_factory=list)


class DriftSummaryResponse(BaseModel):
    total_pending: int
    datasets: Dict[int, int]  # dataset_id -> pending alert count


class InspectionStartResponse(BaseModel):
    task_id: str
    dataset_id: int
    message: str = "巡检任务已启动"


class CronInspectionConfigResponse(BaseModel):
    enabled: bool = False
    cron_expr: str = "0 2 * * *"
    task_id: Optional[int] = None
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    run_count: int = 0
    health_status: str = "unknown"
    last_status: Optional[str] = None
    last_message: Optional[str] = None
    last_error: Optional[str] = None
    notification_channels: List[str] = Field(default_factory=lambda: ["portal"])


class CronInspectionConfigRequest(BaseModel):
    enabled: bool
    cron_expr: str = "0 2 * * *"
    notification_channels: Optional[List[str]] = Field(default_factory=lambda: ["portal"])


class RecommendColumnSemanticRequest(BaseModel):
    """为表中的某个字段推荐语义的请求体。"""

    table_name: str
    column_name: str
    physical_type: Optional[str] = None
    current_term: Optional[str] = None
    current_description: Optional[str] = None
    with_samples: bool = True


class RecommendColumnSemanticResponse(BaseModel):
    """字段语义推荐结果响应体。"""

    dataset_id: int
    dataset_name: Optional[str] = None
    table_name: str
    column_name: str
    physical_exists: bool = False
    physical_type: Optional[str] = None
    comment: Optional[str] = None
    sample_values: List[Any] = Field(default_factory=list)
    current_term: Optional[str] = None
    current_description: Optional[str] = None
    term: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    llm_succeeded: bool = False
    error_message: Optional[str] = None
    sibling_terms: List[str] = Field(default_factory=list)


