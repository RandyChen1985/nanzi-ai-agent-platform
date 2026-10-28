from contextvars import ContextVar
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

# 请求级执行选项（模型/思考/温度等覆盖值），随本轮请求或定时任务下发。
# Structure: { "dry_run": bool, "return_raw_prompt": bool, "model": str, ... }
execution_options_context: ContextVar[Dict[str, Any]] = ContextVar("execution_options_context", default={})

def get_execution_option(key: str, default: Any = None) -> Any:
    """Helper to get a value from the current execution options context."""
    ctx = execution_options_context.get()
    return ctx.get(key, default)

def set_execution_options_context(options: Dict[str, Any]):
    """Set the execution options context for the current request."""
    execution_options_context.set(options or {})

# Define a ContextVar to hold the current executing agent's context
class AgentContext(BaseModel):
    agent_id: str
    agent_name: str
    agent_version: Optional[str] = None
    dataset_ids: List[str] = Field(default_factory=list)
    knowledge_dataset_ids: List[str] = Field(
        default_factory=list,
        description="本轮请求结构化传入的知识库 dataset ID（EmbedChat/API）",
    )
    agent_dataset_ids: List[str] = Field(
        default_factory=list,
        description="智能体绑定且对该智能体用户默认开放的知识库 dataset ID",
    )
    metadata_dataset_ids: List[str] = Field(
        default_factory=list,
        description="本轮生效的 MetaDataset ID（已合并本轮优先/会话回退）",
    )
    metadata_dataset_scope_debug: Dict[str, Any] = Field(
        default_factory=dict,
        description="Schema 工具日志用的数据集范围诊断信息",
    )
    require_explicit_dataset: bool = False
    engine_type: str = "LOCAL"
    engine_config: Optional[Dict[str, Any]] = None
    rag_params: Optional[Dict[str, Any]] = None
    
    # User session info for permission enforcement in tools/services
    user_id: Optional[int] = None
    conversation_id: Optional[str] = None
    browser_session_id: Optional[str] = None
    parent_conversation_id: Optional[str] = None
    child_session_id: Optional[str] = None
    is_admin: bool = False
    api_key: Optional[str] = None
    user_dimensions: Dict[str, Any] = Field(default_factory=dict)
    authorized_attachment_paths: List[str] = Field(default_factory=list)
    current_turn_attachment_paths: List[str] = Field(default_factory=list)
    grounding_evidence_ledger: Optional[Any] = Field(
        default=None,
        description="本轮事实取证账本；委派子智能体共享同一实例",
    )
    knowledge_citation_ledger: Optional[Any] = Field(
        default=None,
        description="本轮知识库引用编号台账（[ID:n] 整轮全局递增）；委派子智能体共享同一实例",
    )
    
    # Execution details for tracing (displayed in frontend)
    trace_id: Optional[str] = None
    parent_trace_id: Optional[str] = None
    agent_max_toolcall_timeout_seconds: Optional[float] = None
    trace_logs: List[str] = Field(default_factory=list)
    trace_buffer: List[Any] = Field(default_factory=list, description="物理执行步骤审计 buffer 引用")

    # Delegation control
    delegation_depth: int = 0
    delegation_run_id: Optional[str] = None
    delegation_tool_filter: Optional[List[str]] = None
    delegation_call_counts: Dict[str, int] = Field(default_factory=dict)
    delegation_agent_call_counts: Dict[str, int] = Field(default_factory=dict)

    # Runtime tool approval (inherited by sub_agent_call delegation)
    permission_options: Dict[str, Any] = Field(default_factory=dict)

    # Agent-level custom Skills allowlist (from published ChatConfig)
    skills_custom: bool = False
    skills: List[str] = Field(default_factory=list)

    # Non-sensitive model identity for runtime diagnostics/tools.
    runtime_model_info: Dict[str, Any] = Field(default_factory=dict)
    runtime_tool_capabilities: List[Dict[str, Any]] = Field(default_factory=list)

    # Queue for streaming sub-agent log/progress chunks back to client
    event_queue: Optional[Any] = None

    # Latest current-turn Todo snapshot for pending confirmation/external resume.
    todo_snapshot: Optional[Dict[str, Any]] = None

    # Current-turn generated-file download URLs that were actually issued by a tool.
    # This list is shared with delegated agent contexts and is used by the final
    # response guard to reject model-invented artifact links.
    published_download_urls: List[str] = Field(default_factory=list)

    # 本轮因「本会话总是允许」而自动放行的工具名队列（判定点入队，工具结果事件出队一次）。
    # 只有真的走了会话授权判定才会入队：只读快通道、approval_mode=allow、被 DENY 的调用
    # 都不经过该判定，因此不会被误标成「自动执行」。
    session_grant_auto_approved: List[str] = Field(default_factory=list)

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
