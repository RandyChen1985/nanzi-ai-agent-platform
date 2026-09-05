import time
import uuid
import contextvars
import logging
from datetime import datetime
from typing import Optional, Any, Dict, List
from app.schemas.agent import AgentExecutionStep

logger = logging.getLogger(__name__)

# 协程与线程安全的 ContextVar，用于维护 Span 调用栈深度与 parent_span_id
current_span_var = contextvars.ContextVar("current_span_var", default=None)


class TraceSpanContext:
    """
    TraceSpan 上下文管理器：
    支持同步与异步写法。自动维护父子 Span ID 嵌套关系并计入 trace_buffer 列表中。
    退出时自动核算纯耗时（ms）并捕获内部异常。
    """

    def __init__(
        self,
        trace_buffer: List[AgentExecutionStep],
        event_type: str,
        span_name: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ):
        self.trace_buffer = trace_buffer
        self.event_type = event_type
        self.span_name = span_name
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.model = model
        self.temperature = temperature

        self.span_id = str(uuid.uuid4())
        self.parent_span_id = None
        self._start_time = 0.0
        self.step: Optional[AgentExecutionStep] = None
        self._token = None

    def enter(self) -> "TraceSpanContext":
        self._start_time = time.perf_counter()
        # 从 ContextVar 读取父级 Span ID
        self.parent_span_id = current_span_var.get()
        # 压栈：将当前 Span ID 设为活动 Trace
        self._token = current_span_var.set(self.span_id)

        # 预先插入一条步骤记录，以便即使后续崩溃也能保留步骤
        step_number = len(self.trace_buffer) + 1
        self.step = AgentExecutionStep(
            step_number=step_number,
            event_type=self.event_type,
            agent_name=self.span_name,
            tool_name=self.tool_name,
            tool_input=self.tool_input,
            model=self.model,
            temperature=self.temperature,
            status="pending",
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            timestamp=datetime.now()
        )
        self.trace_buffer.append(self.step)
        return self

    def exit(self, exc_type=None, exc_val=None, exc_tb=None) -> None:
        if hasattr(self, "_total_llm_calls_ref") and self._total_llm_calls_ref is not None:
            self.update_meta("llm_call_count", self._total_llm_calls_ref[0])
        if self._start_time > 0:
            elapsed_ms = (time.perf_counter() - self._start_time) * 1000
            if self.step:
                self.step.execution_time_ms = elapsed_ms
                if exc_type is not None:
                    self.step.status = "failed"
                    self.step.error_message = str(exc_val)
                else:
                    if self.step.status == "pending":
                        self.step.status = "success"

        # 出栈：恢复父级 Span ID
        if self._token:
            try:
                current_span_var.reset(self._token)
            except ValueError:
                # 流式 async generator 可能在不同 asyncio Context 中恢复；
                # 该 Context 无法消费原 token，此时直接恢复进入本 Span 前的
                # 父值，避免正常聊天因 trace 收尾异常而失败。
                logger.debug(
                    "Trace span token belongs to another context; restoring parent span directly"
                )
                current_span_var.set(self.parent_span_id)
            finally:
                self._token = None

    # --- 支持 with 语法 ---
    def __enter__(self) -> "TraceSpanContext":
        return self.enter()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exit(exc_type, exc_val, exc_tb)

    # --- 支持 async with 语法 ---
    async def __aenter__(self) -> "TraceSpanContext":
        return self.enter()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.exit(exc_type, exc_val, exc_tb)

    # --- 运行时动态修改属性的辅助工具方法 ---
    def set_output(self, output: Any, status: str = "success") -> None:
        """记录步骤的出参/执行结果"""
        if self.step:
            self.step.tool_output = output
            self.step.status = status

    def set_tokens(self, prompt: int, completion: int) -> None:
        """记录当前步骤的模型 Token 消耗"""
        if self.step:
            self.step.prompt_tokens = prompt
            self.step.completion_tokens = completion
            self.step.total_tokens = prompt + completion

    def update_meta(self, key: str, value: Any) -> None:
        """保存额外的调试参数或中间变量映射到 JSON"""
        if self.step:
            if self.step.meta_info is None:
                self.step.meta_info = {}
            self.step.meta_info[key] = value
