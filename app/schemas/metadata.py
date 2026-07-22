from pydantic import BaseModel, ConfigDict
from typing import List, Optional, Dict, Any
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
