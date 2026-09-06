"""
multi_agent_orchestrator.py

多智能体并行编排与结果聚合模块。

将原 AgentService._execute_multi_agent / _synthesize_multi_agent_results 的
实体逻辑内聚至此，AgentService 保留同名代理方法以维持 API 兼容。
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.config import AgentConfigProvider
from app.services.ai.dispatcher import AgentDispatcher
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.error_response_service import sanitize_error_text
from app.services.ai.executors.common import extract_tokens_from_message
from app.services.ai.runtime.agentscope.text_sanitize import sanitize_assistant_stream_text
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.core.orm import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _accumulate_stream_content(full: str, chunk: Dict[str, Any]) -> str:
    """按平台统一流协议合并可见正文。"""
    from app.services.ai.runtime.agentscope.process_narration import accumulate_visible_answer

    return accumulate_visible_answer(full, chunk)


class MultiAgentOrchestrator:
    """并行多智能体编排：解析配置 → 并发执行 → 流式聚合 → LLM 合成。"""

    @staticmethod
    def execute_multi_agent(
        agent_service: Any,
        primary_config: ChatConfig,
        secondary_agent_ids: List[str],
        user_query: str,
        messages: List[Dict[str, str]],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any],
        permission_options: Optional[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]],
        api_key: Optional[str],
        conversation_id: Optional[str] = None,
        turn_decision: Optional[Any] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        并行调度多个专家智能体，流式返回过程日志，最终调用 LLM 聚合各专家输出。

        与 AgentService._execute_multi_agent 保持完全相同的行为契约。
        """
        return _execute_multi_agent_impl(
            agent_service=agent_service,
            primary_config=primary_config,
            secondary_agent_ids=secondary_agent_ids,
            user_query=user_query,
            messages=messages,
            trace_id=trace_id,
            trace_buffer=trace_buffer,
            debug_options=debug_options,
            permission_options=permission_options,
            user_info=user_info,
            api_key=api_key,
            conversation_id=conversation_id,
            turn_decision=turn_decision,
        )

    @staticmethod
    def synthesize_multi_agent_results(
        config: ChatConfig,
        user_query: str,
        agent_outputs: List[Dict[str, str]],
        trace_buffer: List[AgentExecutionStep],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        将多个专家智能体的输出通过主模型合成为统一回答。

        与 AgentService._synthesize_multi_agent_results 保持完全相同的行为契约。
        """
        return _synthesize_multi_agent_results_impl(
            config=config,
            user_query=user_query,
            agent_outputs=agent_outputs,
            trace_buffer=trace_buffer,
        )


async def _execute_multi_agent_impl(
    *,
    agent_service: Any,
    primary_config: ChatConfig,
    secondary_agent_ids: List[str],
    user_query: str,
    messages: List[Dict[str, str]],
    trace_id: str,
    trace_buffer: List[AgentExecutionStep],
    debug_options: Dict[str, Any],
    permission_options: Optional[Dict[str, Any]],
    user_info: Optional[Dict[str, Any]],
    api_key: Optional[str],
    conversation_id: Optional[str],
    turn_decision: Optional[Any],
) -> AsyncGenerator[Dict[str, Any], None]:
    """实体实现：并行调度 + 日志流 + 聚合触发。"""

    # 1. 解析从属智能体配置
    secondary_configs: List[ChatConfig] = []
    async with AsyncSessionLocal() as session:
        for s_id in secondary_agent_ids:
            s_config = await AgentManagerService.get_active_agent_config(
                session, agent_id=s_id
            )
            if s_config:
                secondary_configs.append(s_config)

    # 2. 为每个智能体创建 Executor
    all_configs = [primary_config] + secondary_configs
    executors = []
    for config in all_configs:
        executor = await AgentDispatcher.dispatch(
            config,
            user_query,
            messages,
            trace_id,
            trace_buffer,
            debug_options,
            permission_options,
            user_info,
            conversation_id,
            turn_decision=turn_decision,
        )
        executors.append(executor)

    yield {
        "type": "log",
        "title": "多智能体协作",
        "details": (
            f"正在并行调度 {len(executors)} 个专家智能体: "
            + ", ".join([c.agent_name for c in all_configs])
        ),
        "status": "success",
    }

    # 3. 并发执行，通过 Queue 汇聚流式日志
    queue: asyncio.Queue = asyncio.Queue()

    async def run_executor(executor: Any, config: ChatConfig) -> Dict[str, Any]:
        full_text = ""
        stream_error = None
        try:
            async for chunk in executor.execute(messages):
                chunk_type = chunk.get("type")
                if chunk_type == "error":
                    stream_error = {**chunk, "agent_name": config.agent_name}
                    await queue.put(stream_error)
                    break
                full_text = _accumulate_stream_content(full_text, chunk)
                if chunk_type in {"process_narration", "process_narration_commit"}:
                    # 过程叙述带专家标签转发，让客户端区分并行卡片
                    await queue.put({**chunk, "agent_name": config.agent_name})
                elif chunk_type in {
                    "process_narration_promote",
                    "answer_delta",
                    "retraction",
                }:
                    # 各专家答案仅作为合成输入，不直接转发至主聊天流
                    continue
                elif chunk_type in ("log", "router_log"):
                    if "title" in chunk:
                        chunk = {**chunk, "title": f"[{config.agent_name}] {chunk['title']}"}
                    await queue.put(chunk)
                elif chunk_type == "thinking":
                    await queue.put(chunk)
        except Exception as exc:
            logger.error(
                f"Error in multi-agent sub-task ({config.agent_name}): {exc}",
                exc_info=True,
            )
            await queue.put(
                {
                    "type": "log",
                    "title": f"[{config.agent_name}] 执行异常",
                    "details": sanitize_error_text(exc),
                    "status": "error",
                }
            )
            full_text = f"【{config.agent_name} 执行失败】: {sanitize_error_text(exc)}"
        if stream_error is not None:
            return {"name": config.agent_name, "content": "", "error": stream_error}
        return {"name": config.agent_name, "content": full_text}

    tasks = [
        asyncio.create_task(run_executor(exec_, conf))
        for exec_, conf in zip(executors, all_configs)
    ]
    results_task = asyncio.gather(*tasks, return_exceptions=True)
    stream_error = None

    try:
        # 消费日志队列，同时等待所有子任务完成
        while not results_task.done() or not queue.empty():
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
                if chunk.get("type") == "error" and stream_error is None:
                    stream_error = chunk
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                yield chunk
                queue.task_done()
            except (asyncio.TimeoutError, asyncio.QueueEmpty):
                if results_task.done() and queue.empty():
                    break
                await asyncio.sleep(0.01)

        if stream_error is not None:
            # 已将流式错误直接交给前端；取消剩余专家并跳过合成
            await results_task
            return

        agent_results = await results_task
        agent_outputs = [r for r in agent_results if isinstance(r, dict)]

        # 4. 最终合成
        yield {
            "type": "log",
            "title": "结果聚合",
            "details": "正在汇总各专家意见并组织最终回答...",
            "status": "success",
        }

        # 聚合各专家 executor 的输出标记，透出给 ExecutionStep，使多 Agent 路径
        # 的 has_data_output / tool_run_text 与单 Agent 分支保持语义对称
        # （内部控制事件，不转发给前端）。
        try:
            flag_hdo = any(
                callable(getattr(ex, "resolve_has_data_output", None))
                and bool(ex.resolve_has_data_output())
                for ex in executors
            )
        except Exception:
            flag_hdo = False
        flag_trt = ""
        for ex in executors:
            try:
                resolve_trt = getattr(ex, "resolve_tool_run_text", None)
                if callable(resolve_trt):
                    text = resolve_trt() or ""
                    if text:
                        flag_trt = text
                        break
            except Exception:
                continue
        yield {
            "type": "multi_agent_output_flag",
            "has_data_output": flag_hdo,
            "tool_run_text": flag_trt or None,
        }

        async for chunk in agent_service._synthesize_multi_agent_results(
            primary_config,
            user_query,
            agent_outputs,
            trace_buffer,
        ):
            yield chunk
    finally:
        # 生成器被提前关闭/取消（客户端断连、外层 CancelledError）时，确保子任务被清理，
        # 避免持有 LLM/执行器连接的 asyncio task 在无人消费的队列上继续空转泄漏。
        for task in tasks:
            if not task.done():
                task.cancel()
        if results_task is not None and not results_task.done():
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:  # 收尾阶段异常不应再次污染主流程
                logger.warning("multi-agent cleanup raised", exc_info=True)


async def _synthesize_multi_agent_results_impl(
    *,
    config: ChatConfig,
    user_query: str,
    agent_outputs: List[Dict[str, str]],
    trace_buffer: List[AgentExecutionStep],
) -> AsyncGenerator[Dict[str, Any], None]:
    """实体实现：将多专家输出通过主模型合成为统一回答。"""

    outputs_str = "".join(
        f"### 专家智能体: {out['name']}\n{out['content']}\n\n"
        for out in agent_outputs
    )

    system_prompt = AgentServicePrompts.MULTI_AGENT_SYNTHESIS_SYSTEM
    human_content = AgentServicePrompts.multi_agent_synthesis_human(
        user_query, outputs_str
    )

    llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=config)

    lc_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]

    start_synthesis = time.time()
    full_content = ""
    accumulated_msg = None
    async for chunk in llm.astream(lc_messages):
        if accumulated_msg is None:
            accumulated_msg = chunk
        else:
            accumulated_msg += chunk
        content = sanitize_assistant_stream_text(str(chunk.content or ""))
        if content:
            full_content += content
            yield {"type": "answer_delta", "content": content, "phase": "synthesis"}

    tokens = extract_tokens_from_message(accumulated_msg)
    step_number = max((s.step_number for s in trace_buffer), default=0) + 1
    s_model = getattr(llm, "model_name", config.synthesis_model_name or config.model_name)
    s_temp = config.synthesis_temperature or config.temperature
    trace_buffer.append(
        AgentExecutionStep(
            step_number=step_number,
            event_type="synthesis",
            agent_name=config.agent_name,
            model=str(s_model),
            temperature=float(s_temp or 0),
            tool_output={"content": full_content, "multi_agent_synthesis": True},
            raw_log=full_content,
            execution_time_ms=(time.time() - start_synthesis) * 1000,
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            total_tokens=tokens["total_tokens"],
            timestamp=datetime.fromtimestamp(start_synthesis),
        )
    )
