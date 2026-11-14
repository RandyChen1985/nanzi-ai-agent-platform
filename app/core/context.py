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


# 当前正在执行的工具级运行时覆盖（温度等），由工具调用包装器 set/reset。
# 只有内部会自己调 LLM 的工具（如 read_image）会读取它；纯函数工具拿到 None。
tool_runtime_override_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "tool_runtime_override_context", default=None
)


def get_current_tool_runtime_override() -> Optional[Dict[str, Any]]:
    """读取当前工具级运行时覆盖；不在工具调用栈内时为 None。"""
    return tool_runtime_override_context.get()


def get_current_tool_temperature() -> Optional[float]:
    """读取当前工具配置的温度覆盖；未配置或不在工具栈内返回 None（0.0 是合法值）。"""
    override = tool_runtime_override_context.get() or {}
    value = override.get("temperature")
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def set_tool_runtime_override(override: Optional[Dict[str, Any]]):
    """设置当前工具级运行时覆盖，返回 token 供 finally 阶段 reset。"""
    return tool_runtime_override_context.set(override)


def reset_tool_runtime_override(token: Any) -> None:
    """恢复调用前的工具级覆盖。"""
    try:
        tool_runtime_override_context.reset(token)
    except (ValueError, RuntimeError):
        # token 跨上下文 reset 会抛错，退化为清空，避免污染后续调用。
        tool_runtime_override_context.set(None)

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
    bound_metadata_dataset_ids: List[str] = Field(
        default_factory=list,
        description="智能体工具级绑定的 MetaDataset ID（优先于本轮手选；schema 检索与 SQL 执行同口径）",
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
    credential_user_query: str = Field(default="", exclude=True, repr=False)
    scheduled_task_id: int | None = Field(default=None, exclude=True)
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
    #: 本轮内每个子代理的子会话历史缓存，键是归一化后的 agent 名。
    #: 只在 ``resume=true`` 时被读取，值是 ``[{"role": "user"/"assistant", "content": ...}]``；
    #: 进程内、随本轮上下文销毁，不做持久化——跨轮次的子会话不是这个字段的职责。
    delegation_session_histories: Dict[str, List[Dict[str, str]]] = Field(default_factory=dict)
    #: 续聊次数单独计数，不与 ``delegation_agent_call_counts``（重查额度）共用。
    delegation_resume_call_counts: Dict[str, int] = Field(default_factory=dict)
    #: 当前子代理所属的批量委派批次（`batch_id` / `index` / `total`），单次委派为 None。
    #: 由 `sub_agent_batch_call` 在派发前写进**该任务自己的**上下文副本，
    #: 前端据此把同一批并行的子代理收成一个块——没有它，并行的几个子代理只能一条条
    #: 平铺，用户看不出它们是同时跑的。
    delegation_batch: Optional[Dict[str, Any]] = None
    #: 本轮已经占用了提问卡的委派 run_id。``UserQuestionStore`` 每个会话只有一个
    #: active 槽位，batch 并行时多个子代理同时提问会把先来的标成 superseded 静默吃掉；
    #: 用这个共享标记保证同一时刻只放行一张卡（deep=False 的 model_copy 共享同一实例）。
    delegation_pending_questions: Dict[str, str] = Field(default_factory=dict)

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
