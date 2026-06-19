from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List, Union
from datetime import datetime

# --- Agent Management Schemas ---

class ToolConfigItem(BaseModel):
    """Specific configuration for a tool instance usage."""
    name: str
    enabled: bool = True
    model_name: Optional[str] = None  # Override Agent's model
    temperature: Optional[float] = None # Override Agent's temperature
    description_override: Optional[str] = None # Optional: Allow prompting tuning
    engine_config_override: Optional[Dict[str, Any]] = None

class AIAgentVersionBase(BaseModel):
    version_number: Optional[int] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = 0.0
    synthesis_model_name: Optional[str] = None # NEW: Separate synthesizer
    synthesis_temperature: Optional[float] = None # NEW: Separate synthesizer temp
    system_prompt: str
    tools: List[Union[str, ToolConfigItem]] = Field(default_factory=list)
    status: str = "DRAFT"
    comment: Optional[str] = None

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
    is_system: Optional[bool] = False
    sort_order: Optional[int] = 0
    is_enabled: Optional[bool] = True
    engine_type: str = "LOCAL"
    engine_config: Optional[Dict[str, Any]] = None

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
    # versions: List[AIAgentVersionResponse] = [] # Optional

    model_config = ConfigDict(from_attributes=True)

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
