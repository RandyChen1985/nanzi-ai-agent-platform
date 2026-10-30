from pydantic import BaseModel, Field, ConfigDict, StrictInt, model_validator, field_validator
from typing import Optional, Dict, Any, List, Union
from datetime import datetime
from app.services.ai.agent_types import AgentType

# --- Agent Management Schemas ---

class ToolConfigItem(BaseModel):
    """Specific configuration for a tool instance usage."""
    name: str
    enabled: bool = True
    model_name: Optional[str] = None  # Override Agent's model
    temperature: Optional[float] = None # Override Agent's temperature
    description_override: Optional[str] = None # Optional: Allow prompting tuning
    engine_config_override: Optional[Dict[str, Any]] = None
    metadata_dataset_ids: Optional[List[str]] = None  # get_dataset_schema 可见元数据集范围

    @model_validator(mode="after")
    def _validate_metadata_dataset_ids(self):
        """绑定值必须是数据集 ID。

        运行时 ``get_dataset_schema`` 入口只认纯数字 ID，写成数据集名称会被静默丢弃，
        等于没绑定——配置页却仍然显示「已绑定 N 个」，管理员无从发现。这里在保存时
        直接拒绝，把静默失效变成可见错误。
        """
        if self.name != "get_dataset_schema" or not self.metadata_dataset_ids:
            return self
        invalid = [
            str(item).strip()
            for item in self.metadata_dataset_ids
            if not str(item).strip().isdigit()
        ]
        if invalid:
            raise ValueError(
                "metadata_dataset_ids 只接受数据集 ID（纯数字），"
                f"以下取值无法识别：{'、'.join(invalid)}。请重新选择数据集。"
            )
        return self

class AIAgentVersionBase(BaseModel):
    version_number: Optional[int] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = 0.0
    synthesis_model_name: Optional[str] = None # NEW: Separate synthesizer
    synthesis_temperature: Optional[float] = None # NEW: Separate synthesizer temp
    # 是否允许在输入框手动切换模型；默认允许，历史版本未显式配置时同样视为允许
    allow_model_switch: bool = True
    system_prompt: str
    tools: List[Union[str, ToolConfigItem]] = Field(default_factory=list)
    toolcall_timeout_seconds: Optional[StrictInt] = Field(default=None, ge=1, le=86400)
    skills_custom: bool = False
    skills: List[str] = Field(default_factory=list)
    welcome_config: Optional[Dict[str, Any]] = None
    status: str = "DRAFT"
    comment: Optional[str] = None

    @field_validator("tools", mode="before")
    @classmethod
    def coerce_tools_list(cls, value):
        # DB 历史行 tools 可能是 NULL（早期插入路径没写这一列）；
        # from_attributes 构造 payload 时若不归一，会直接校验失败
        if value is None:
            return []
        return value

    @field_validator("skills", mode="before")
    @classmethod
    def coerce_skills_list(cls, value):
        # DB 历史行 skills 可能为 NULL；from_attributes 时需归一成 []
        if value is None:
            return []
        return value

    @field_validator("skills_custom", mode="before")
    @classmethod
    def coerce_skills_custom(cls, value):
        if value is None:
            return False
        return bool(value)

    @model_validator(mode="after")
    def normalize_skills_when_not_custom(self):
        if not self.skills_custom:
            self.skills = []
        return self

class AIAgentVersionResponse(AIAgentVersionBase):
    id: str
    agent_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class AIAgentBase(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    capabilities: Optional[List[str]] = []
    agent_type: AgentType = AgentType.GENERAL
    is_system: Optional[bool] = False
    sort_order: Optional[int] = 0
    is_enabled: Optional[bool] = True
    engine_type: str = "LOCAL"
    engine_config: Optional[Dict[str, Any]] = None

class AgentDuplicateRequest(BaseModel):
    """复制智能体时的目标标识与显示名；其余配置一律从源智能体复制。"""

    name: str = Field(..., min_length=1, max_length=100, description="新智能体的物理标识符（全局唯一）")
    display_name: str = Field(..., min_length=1, max_length=100, description="新智能体的显示名称")

class AIAgentReorderItem(BaseModel):
    id: str
    sort_order: int

class AIAgentReorderRequest(BaseModel):
    items: List[AIAgentReorderItem]

class AgentModelIssue(BaseModel):
    """智能体绑定模型在当前环境不可用的原因（取值见 agent_model_health）。"""

    code: str
    message: str
    model_name: str = ""


class AgentToolIssue(BaseModel):
    """已发布版本里运行时已失效的工具引用摘要（列表页横幅展示用）。"""

    code: str = "stale_tools"
    message: str
    count: int
    names: List[str] = Field(default_factory=list)
    version_id: str = ""


class AIAgentResponse(AIAgentBase):
    id: str
    is_system: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    # 创建者显示名（real_name 优先，回落 user_name）。列表接口批量反查后填充，
    # 供管理员在卡片上辨认归属，不必再点进详情。
    created_by_name: Optional[str] = None
    owner_group: Optional[str] = None
    is_editable: bool = True # Computed field: true if user is owner or admin
    # 版本内容可编辑：系统级智能体的所有者仍可建版本/改草稿/存草稿
    can_edit_versions: bool = True
    # 能否把草稿发布上线：系统级对非管理员恒为 False，只能存草稿
    can_publish_version: bool = True
    execution_count: int = 0
    # 已发布版本能力摘要（列表卡片展示用；无发布版时为 null）
    tool_count: Optional[int] = None
    mcp_count: Optional[int] = None
    skill_count: Optional[int] = None
    skills_custom: Optional[bool] = None
    # 仅在智能体显式绑定（非全局策略）时返回计数，否则为 null
    metadata_dataset_count: Optional[int] = None
    knowledge_base_count: Optional[int] = None
    # 线上版本号：卡片上要告诉所有者「现在生效的是哪一版」，改草稿时好心里有数
    published_version_number: Optional[int] = None
    # 复制智能体时因无权使用而被跳过的工具引用（仅复制接口返回，其它接口为 null）
    dropped_tool_refs: Optional[List[Dict[str, Any]]] = None
    # 权属人账号是否仍然启用：false 表示这个智能体已经没人能编辑了
    owner_active: Optional[bool] = None
    readiness_ready: bool = False
    readiness_missing: List[str] = Field(default_factory=list)
    # 模型配置异常：null 表示正常；仅在列表接口计算，避免/health 之类的轻量接口多查库
    model_issue: Optional[AgentModelIssue] = None
    # 已发布版本的工具失效摘要：null 表示正常或没有已发布版本
    tool_issue: Optional[AgentToolIssue] = None
    onboarding_step: str = "COMPLETE"
    # 系统级流程状态：NONE / PENDING_REVIEW。
    # 这两列刻意不在 ORM 声明（见 agent_system_columns.py），由 agent_manager 在返回前
    # 用原生 SQL 批量填充到对象上；这里给默认值，保证未迁移时序列化不炸。
    system_status: str = "NONE"
    system_pending_request_id: Optional[str] = None
    # 待审申请单的提交时间（仅 PENDING_REVIEW 有值），卡片冻结条据此显示「等了多久」
    system_requested_at: Optional[datetime] = None
    # 已发布版本的「是否允许手动切换模型」；None 表示无已发布版本，前端按允许处理
    allow_model_switch: Optional[bool] = None
    # versions: List[AIAgentVersionResponse] = [] # Optional

    model_config = ConfigDict(from_attributes=True)


class AgentOnboardingCreateRequest(AIAgentBase):
    onboarding_key: str = Field(min_length=8, max_length=64)


class AgentOnboardingResponse(BaseModel):
    agent: AIAgentResponse
    version: AIAgentVersionResponse
    onboarding_step: str
    template_fallback: bool = False
    # 模板里因当前用户无权使用而被跳过的 MCP 工具引用
    dropped_tool_refs: List[Dict[str, Any]] = Field(default_factory=list)

# --- Chat Execution Schemas ---

class ChatConfig(BaseModel):
    """Resolved configuration for a single chat turn"""
    agent_id: str
    agent_name: str
    agent_display_name: Optional[str] = None
    agent_type: AgentType = AgentType.GENERAL
    description: str = ""
    project_name: Optional[str] = None
    agent_version: Optional[str] = None
    model_name: Optional[str]
    temperature: Optional[float]
    synthesis_model_name: Optional[str] = None
    synthesis_temperature: Optional[float] = None
    # 版本级开关：False 表示该智能体版本禁止用户在输入框手动切换模型
    allow_model_switch: bool = True
    system_prompt: str
    tools: List[Union[str, ToolConfigItem]]
    toolcall_timeout_seconds: Optional[int] = None
    skills_custom: bool = False
    skills: List[str] = Field(default_factory=list)
    welcome_config: Optional[Dict[str, Any]] = None
    capabilities: List[str] = Field(default_factory=list)
    engine_type: str = "LOCAL"
    engine_config: Optional[Dict[str, Any]] = None
    agent_dataset_ids: Optional[List[str]] = None

class AgentExecutionStep(BaseModel):
    step_number: int
    event_type: str  # thought, tool_call, tool_result, final_answer, error
    agent_name: str = "ChatBI"
    model: Optional[str] = None # The specific model used for this step
    temperature: Optional[float] = None
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[Any] = None
    raw_log: Optional[str] = None # Store raw LLM content for debugging
    execution_time_ms: Optional[float] = None
    status: str = "success"
    error_message: Optional[str] = None
    prompt_tokens: Optional[int] = 0
    completion_tokens: Optional[int] = 0
    total_tokens: Optional[int] = 0
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    meta_info: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class TraceLogResponse(BaseModel):
    trace_id: str
    total_steps: int
    steps: list[AgentExecutionStep]
    history: Optional['AgentExecutionHistoryResponse'] = None # Include query/summary context

class AgentExecutionHistoryResponse(BaseModel):
    id: int
    trace_id: str
    agent_id: str
    conversation_id: Optional[str] = None
    project_name: Optional[str] = None
    username: Optional[str] = None
    query: Optional[str] = None
    summary: Optional[str] = None
    reasoning_content: Optional[str] = None
    process_timeline: Optional[list] = None
    status: str
    agent_version: Optional[str] = None
    model_id: Optional[str] = None
    execution_time_ms: Optional[float] = None
    prompt_tokens: Optional[int] = 0
    completion_tokens: Optional[int] = 0
    total_tokens: Optional[int] = 0
    has_data_output: bool = False
    reusable_result_id: Optional[str] = None
    reusable_result_status: Optional[str] = None
    turn_count: Optional[int] = None # 新增：对话轮数
    created_at: datetime
    agent_name: Optional[str] = None
    agent_display_name: Optional[str] = None
    # 智能体专属头像（短路径）；为空表示该智能体未单独设置，前端继承全局 AI 形象
    agent_avatar_url: Optional[str] = None
    # 知识库引用详情（Redis 旁路存储，见 knowledge_citation_store）；缺失表示已过期
    citations: Optional[list] = None

    model_config = ConfigDict(from_attributes=True)

class AgentExecutionHistoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AgentExecutionHistoryResponse]


# ---------------------------------------------------------------------------
# 系统级发布（申请 / 审核 / 发布 / 下架 / 历史）
# ---------------------------------------------------------------------------


class AgentSystemRequestCreate(BaseModel):
    """提交发布申请。理由可选，最多 500 字，供审核人参考。"""

    comment: Optional[str] = Field(default=None, max_length=500)


class AgentSystemReviewRequest(BaseModel):
    """审核动作。通过时 comment 可选；驳回时必填（服务层校验）。"""

    comment: Optional[str] = Field(default=None, max_length=500)


class AgentSystemPublishRequest(BaseModel):
    """直接发布为系统级。不涉及版本选择——发布只改状态。"""

    reason: Optional[str] = Field(default=None, max_length=500)
    # 上次下架若通知过用户，这次重新上架默认也通知；这里允许显式关掉
    notify_restore: bool = True


class AgentSystemUnpublishRequest(BaseModel):
    """下架。原因必填；notify 默认开，关掉时只在事件里记受影响人数。"""

    reason: str = Field(min_length=1, max_length=500)
    notify: bool = True


class AgentSystemRequestItem(BaseModel):
    id: str
    agent_id: str
    agent_name: str
    # 智能体被删除后为 None：孤儿申请单仍要出现在待审列表里，否则角标清不掉
    agent_identifier: Optional[str] = None
    engine_type: Optional[str] = None
    agent_missing: bool = False
    status: str
    # SYSTEM_PUBLISH=申请发布为系统级；VERSION_RELEASE=申请把某版本发布上线
    request_type: Optional[str] = "SYSTEM_PUBLISH"
    # VERSION_RELEASE 时告诉审核人「要上线的是哪一版」
    version_number: Optional[int] = None
    request_comment: Optional[str] = None
    requested_by: Optional[int] = None
    # 审核人要看「谁提交的」，只有 user_id 等于没法判断
    requested_by_name: Optional[str] = None
    requested_at: datetime
    waiting_hours: float = 0


class AgentSystemRequestDetail(AgentSystemRequestItem):
    snapshot: Optional[dict] = None
    requested_by_name: Optional[str] = None
    reviewed_by: Optional[int] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_comment: Optional[str] = None


class AgentSystemHistoryEntry(BaseModel):
    kind: str  # request / event
    id: str
    at: Optional[datetime] = None
    # at 对已审完的申请取审核时间，原始提交时间另存，便于同时展示「提交于 / 审于」
    requested_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    status: Optional[str] = None
    action: Optional[str] = None
    actor_name: Optional[str] = None
    comment: Optional[str] = None
    review_comment: Optional[str] = None
    reason: Optional[str] = None
    version_number: Optional[int] = None
    affected_user_count: Optional[int] = None
    notified: Optional[bool] = None
    snapshot: Optional[dict] = None


class AgentSystemHistoryResponse(BaseModel):
    total: int
    items: List[AgentSystemHistoryEntry]


class AgentSystemUnpublishPreview(BaseModel):
    agent_id: str
    affected_user_count: int
    sample_user_names: List[str] = []
