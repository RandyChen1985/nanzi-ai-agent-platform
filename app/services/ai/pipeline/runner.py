"""
PipelineRunner: 负责编排和调度各个流水线步骤（Pipeline Steps）按序协同执行，
提供完整的生命周期控制、异常降级、流式 Chunk 透传与尾端安全收敛。
"""
from typing import Any, AsyncGenerator, Dict, List, Optional
import asyncio
import logging

from app.services.ai.pipeline.base import BasePipelineStep
from app.services.ai.pipeline.context import PipelineContext
from app.services.ai.pipeline.steps.preflight_step import PreflightStep
from app.services.ai.pipeline.steps.context_step import ContextStep
from app.services.ai.pipeline.steps.route_step import RouteStep
from app.services.ai.pipeline.steps.assemble_step import AssembleStep
from app.services.ai.pipeline.steps.execution_step import ExecutionStep
from app.services.ai.pipeline.steps.finalize_step import FinalizeStep

logger = logging.getLogger(__name__)

# 中断/非继续推导状态集合
TERMINAL_OR_SHORT_CIRCUIT_STATUSES = {
    "quota_exceeded",
    "cancelled",
    "empty_request",
    "no_agent_config",
    "answered_directly",
    "denied",
    "error",
}


class PipelineRunner:
    """AgentService 流水线编排调度器"""

    def __init__(self, steps: Optional[List[BasePipelineStep]] = None):
        self.steps: List[BasePipelineStep] = steps or []

    def add_step(self, step: BasePipelineStep) -> "PipelineRunner":
        self.steps.append(step)
        return self

    @classmethod
    def create_default_pipeline(cls, agent_service: Any = None) -> "PipelineRunner":
        """构建标准 6 阶段执行管道"""
        return cls([
            PreflightStep(agent_service),
            ContextStep(agent_service),
            RouteStep(agent_service),
            AssembleStep(agent_service),
            ExecutionStep(agent_service),
            FinalizeStep(agent_service),
        ])

    async def run(self, context: PipelineContext) -> AsyncGenerator[Dict[str, Any], None]:
        """按序驱动流水线步骤，向客户端流式输出 SSE chunks"""
        run_handle = context.run_handle

        for idx, step in enumerate(self.steps):
            is_finalize = isinstance(step, FinalizeStep) or (idx == len(self.steps) - 1)

            # 若已处于提前结束状态，跳过中间执行步骤，直达 Finalize 步骤
            if context.execution_status in TERMINAL_OR_SHORT_CIRCUIT_STATUSES and not is_finalize:
                continue

            try:
                async for chunk in step.run(context):
                    if run_handle is not None and getattr(run_handle, "cancelled", False):
                        context.execution_status = "cancelled"
                        raise asyncio.CancelledError("User cancelled execution run")
                    if isinstance(chunk, dict):
                        from app.services.ai.agent_service import _track_process_timeline

                        _track_process_timeline(
                            context.shared_state.get("process_timeline"),
                            chunk,
                        )
                    yield chunk
            except asyncio.CancelledError:
                context.execution_status = "cancelled"
                logger.info(f"[PipelineRunner] Run cancelled for trace: {context.trace_id}")
                # 若尚未执行到 FinalizeStep，确保 FinalizeStep 能够收拢
                if not is_finalize:
                    finalize_step = next(
                        (s for s in self.steps if isinstance(s, FinalizeStep)),
                        FinalizeStep(),
                    )
                    async for chunk in finalize_step.run(context):
                        yield chunk
                raise
            except Exception as e:
                from app.services.ai.agent_service import (
                    _enrich_terminal_error_chunk,
                    _track_process_timeline,
                )
                from app.services.ai.error_response_service import sanitize_error_text

                logger.error(
                    "[PipelineRunner] Step %s failed: %s",
                    step.__class__.__name__,
                    sanitize_error_text(e),
                )
                context.execution_status = "error"
                context.shared_state["execution_status"] = "error"
                agent_config = context.agent_config or context.shared_state.get("agent_config")
                error_chunk = await _enrich_terminal_error_chunk(
                    {
                        "type": "error",
                        "status": "error",
                        "content": "流水线处理异常",
                        "phase": step.__class__.__name__,
                    },
                    config=agent_config,
                    model_name=getattr(agent_config, "model_name", None),
                    source_exception=e,
                )
                _track_process_timeline(
                    context.shared_state.get("process_timeline"),
                    error_chunk,
                )
                yield error_chunk
                if not is_finalize:
                    finalize_step = next(
                        (s for s in self.steps if isinstance(s, FinalizeStep)),
                        FinalizeStep(),
                    )
                    async for chunk in finalize_step.run(context):
                        yield chunk
                return
