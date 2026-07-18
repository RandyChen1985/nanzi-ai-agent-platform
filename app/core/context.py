from contextvars import ContextVar
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

# Define a ContextVar to hold request-scoped debug options
# Structure: { "dry_run": bool, "return_raw_prompt": bool, "model_override": str, ... }
debug_context: ContextVar[Dict[str, Any]] = ContextVar("debug_context", default={})

def get_debug_option(key: str, default: Any = None) -> Any:
    """Helper to get a value from the current debug context."""
    ctx = debug_context.get()
    return ctx.get(key, default)

def set_debug_context(options: Dict[str, Any]):
    """Set the debug context for the current request."""
    debug_context.set(options or {})

# Define a ContextVar to hold the current executing agent's context
class AgentContext(BaseModel):
    agent_id: str
    agent_name: str
    dataset_ids: List[str] = Field(default_factory=list)
    knowledge_dataset_ids: List[str] = Field(
        default_factory=list,
        description="本轮请求结构化传入的知识库 dataset ID（EmbedChat/API）",
    )
    require_explicit_dataset: bool = False
    engine_type: str = "LOCAL"
    engine_config: Optional[Dict[str, Any]] = None
    rag_params: Optional[Dict[str, Any]] = None
    
    # User session info for permission enforcement in tools/services
    user_id: Optional[int] = None
    conversation_id: Optional[str] = None
    is_admin: bool = False
    api_key: Optional[str] = None
    user_dimensions: Dict[str, Any] = Field(default_factory=dict)
    authorized_attachment_paths: List[str] = Field(default_factory=list)
    current_turn_attachment_paths: List[str] = Field(default_factory=list)
    grounding_evidence_ledger: Optional[Any] = Field(
        default=None,
        description="本轮事实取证账本；委派子智能体共享同一实例",
    )
    
    # Execution details for tracing (displayed in frontend)
    trace_logs: List[str] = Field(default_factory=list)
    trace_buffer: List[Any] = Field(default_factory=list, description="物理执行步骤审计 buffer 引用")

    # Delegation control
    delegation_depth: int = 0
    delegation_call_counts: Dict[str, int] = Field(default_factory=dict)
    delegation_agent_call_counts: Dict[str, int] = Field(default_factory=dict)

    # Runtime tool approval (inherited by sub_agent_call delegation)
    permission_options: Dict[str, Any] = Field(default_factory=dict)

    # Agent-level custom Skills allowlist (from published ChatConfig)
    skills_custom: bool = False
    skills: List[str] = Field(default_factory=list)

    # Queue for streaming sub-agent log/progress chunks back to client
    event_queue: Optional[Any] = None

agent_context: ContextVar[Optional[AgentContext]] = ContextVar("agent_context", default=None)

def get_current_agent_context() -> Optional[AgentContext]:
    """Get the current agent context object."""
    return agent_context.get()

def set_agent_context(context: AgentContext):
    """Set the agent context for the current execution."""
    agent_context.set(context)

def get_current_agent_config(key: str, default: Any = None) -> Any:
    """Helper to get a value from the current agent context (for backward compatibility)."""
    ctx = agent_context.get()
    if not ctx:
        return default
    return getattr(ctx, key, default)
