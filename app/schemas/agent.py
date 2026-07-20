from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
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

class AIAgentVersionBase(BaseModel):
    version_number: Optional[int] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = 0.0
    synthesis_model_name: Optional[str] = None # NEW: Separate synthesizer
    synthesis_temperature: Optional[float] = None # NEW: Separate synthesizer temp
    system_prompt: str
    tools: List[Union[str, ToolConfigItem]] = Field(default_factory=list)
    skills_custom: bool = False
    skills: List[str] = Field(default_factory=list)
    status: str = "DRAFT"
    comment: Optional[str] = None

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

class AIAgentReorderItem(BaseModel):
    id: str
    sort_order: int

class AIAgentReorderRequest(BaseModel):
    items: List[AIAgentReorderItem]

class AIAgentResponse(AIAgentBase):
    id: str
    is_system: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    owner_group: Optional[str] = None
    is_editable: bool = True # Computed field: true if user is owner or admin
    execution_count: int = 0
    # 已发布版本能力摘要（列表卡片展示用；无发布版时为 null）
    tool_count: Optional[int] = None
    mcp_count: Optional[int] = None
    skill_count: Optional[int] = None
    skills_custom: Optional[bool] = None
    # 仅在智能体显式绑定（非全局策略）时返回计数，否则为 null
    metadata_dataset_count: Optional[int] = None
    knowledge_base_count: Optional[int] = None
    readiness_ready: bool = False
    readiness_missing: List[str] = Field(default_factory=list)
    onboarding_step: str = "COMPLETE"
    # versions: List[AIAgentVersionResponse] = [] # Optional

    model_config = ConfigDict(from_attributes=True)


class AgentOnboardingCreateRequest(AIAgentBase):
    onboarding_key: str = Field(min_length=8, max_length=64)


class AgentOnboardingResponse(BaseModel):
    agent: AIAgentResponse
    version: AIAgentVersionResponse
    onboarding_step: str
    template_fallback: bool = False

# --- Chat Execution Schemas ---

class ChatConfig(BaseModel):
    """Resolved configuration for a single chat turn"""
    agent_id: str
    agent_name: str
    agent_display_name: Optional[str] = None
    agent_version: Optional[str] = None
    model_name: Optional[str]
    temperature: Optional[float]
    synthesis_model_name: Optional[str] = None
    synthesis_temperature: Optional[float] = None
    system_prompt: str
    tools: List[Union[str, ToolConfigItem]]
    skills_custom: bool = False
    skills: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    engine_type: str = "LOCAL"
    engine_config: Optional[Dict[str, Any]] = None

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
    username: Optional[str] = None
    query: Optional[str] = None
    summary: Optional[str] = None
    status: str
    agent_version: Optional[str] = None
    model_id: Optional[str] = None
    execution_time_ms: Optional[float] = None
    prompt_tokens: Optional[int] = 0
    completion_tokens: Optional[int] = 0
    total_tokens: Optional[int] = 0
    turn_count: Optional[int] = None # 新增：对话轮数
    created_at: datetime
    agent_name: Optional[str] = None
    agent_display_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class AgentExecutionHistoryListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[AgentExecutionHistoryResponse]
