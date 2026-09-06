"""Pipeline execution context for AgentService turn execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep


@dataclass
class PipelineContext:
    """Carries full state across all pipeline steps in a single chat turn."""

    # 1. User & Request Input
    messages: List[Dict[str, Any]]
    user_info: Optional[Dict[str, Any]] = None
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    version_id: Optional[str] = None
    conversation_id: Optional[str] = None
    api_key: Optional[str] = None
    enable_multi_agent: bool = True
    debug_options: Dict[str, Any] = field(default_factory=dict)
    permission_options: Optional[Dict[str, Any]] = None
    knowledge_dataset_ids: Optional[List[str]] = None
    metadata_dataset_ids: Optional[List[str]] = None
    reusable_result_id: Optional[str] = None
    quick_context: Optional[Dict[str, Any]] = None
    request_observability: Optional[Dict[str, Any]] = None

    # 2. Runtime Tracking & Identifiers
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    lane_user_id: Any = None
    trace_buffer: List[AgentExecutionStep] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    agent_max_toolcall_timeout_seconds: Optional[float] = None

    # 3. Turn & Generation State
    user_query: str = ""
    full_response_content: str = ""
    full_reasoning_content: str = ""
    tool_run_text: Optional[str] = None
    execution_status: str = "success"
    has_data_output: Optional[bool] = None
    user_question_cancelled: bool = False
    cancelled_cancellation_message: Optional[str] = None
    agent_config: Any = None
    turn_decision: Any = None
    ltm_profile: Optional[str] = None
    ltm_loaded_data: Optional[Dict[str, Any]] = None

    # 4. Shared State (bridges to executors & runners)
    shared_state: Dict[str, Any] = field(
        default_factory=lambda: {
            "agent_config": None,
            "execution_status": "success",
            "process_timeline": [],
            "preparation_started_at": None,
            "preparation_ready": False,
        }
    )

    # 5. Lock & Run Handles
    run_handle: Any = None
    is_scheduled_task: bool = False
    performance_tracker: Any = None

    # 6. Aggregated Metrics
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
