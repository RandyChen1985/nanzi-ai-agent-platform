import time
import uuid
import json
import logging
import asyncio
import re
from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime

from app.services.ai.runtime.agentscope.compat import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from app.schemas.agent import ChatConfig, AgentExecutionStep
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.common import (
    append_system_instruction,
    convert_history_to_messages,
    extract_tokens_from_message,
    normalize_messages_for_llm,
    parse_xml_tool_calls,
    tools_include_named,
    MODEL_STREAM_MAX_RETRIES,
    build_stream_retry_log,
    build_stream_error_log,
    is_retryable_stream_error,
)
from app.services.ai.executors.prompts import GeneralChatPrompts
from app.services.ai.turn_classifier import TurnType

logger = logging.getLogger(__name__)


class GeneralAgentRunner(BaseExecutor):
    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        route_hints: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id)
        self.intent_info = None
        self.intent_elapsed_ms = 0.0
        self.turn_classification = None
        self._requires_knowledge_search = False
        self.route_hints = route_hints or {}

    async def execute(
        self,
        history: List[Dict[str, str]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.multimodal_support import (
            ensure_multimodal_compatible,
            resolve_runtime_model_name,
        )

        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {"content": incompatible_msg, "status": "error"}
            return

        # 1. Prepare LLM
        configured_tools = self.config.tools or []
        tools = []
        if configured_tools:
            tools = await ToolRegistry.get_tools(configured_tools)

        system_tools = ToolRegistry.get_system_implicit_tools()
        if system_tools:
            tools.extend(system_tools)

        if self._requires_knowledge_search or (
            getattr(self, "turn_classification", None)
            and self.turn_classification.turn_type == TurnType.KNOWLEDGE
        ):
            self._requires_knowledge_search = True

        if self._requires_knowledge_search and not tools_include_named(tools, "search_knowledge_base"):
            kb_tool = await ToolRegistry.get_tool("search_knowledge_base")
            if kb_tool:
                tools.append(kb_tool)

        # 2. Build Messages
        system_content = self.config.system_prompt or ""
        route_hint = GeneralChatPrompts.route_hints(self.route_hints)
        if route_hint:
            system_content = f"{route_hint}\n\n{system_content}"
        if self._requires_knowledge_search:
            system_content = f"{GeneralChatPrompts.KNOWLEDGE_TURN_SYSTEM_HINT}\n\n{system_content}"
        runtime_messages = [SystemMessage(content=system_content)]
        runtime_messages.extend(convert_history_to_messages(history))
        runtime_messages = normalize_messages_for_llm(runtime_messages)

        # 3. Execution Mode Selection
        if not tools:
            # --- Simple Mode (No Tools) ---
            start_synthesis = time.time()
            yield {"type": "log", "id": f"syn_s_{uuid.uuid4().hex[:8]}", "title": "📝 准备回答", "details": "正在生成回答...", "status": "success"}
            
            # Use Synthesizer for simple mode
            llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
            
            full_content = ""
            content_emitted = False
            accumulated_msg = None
            stream_succeeded = False
            for stream_attempt in range(MODEL_STREAM_MAX_RETRIES):
                accumulated_msg = None
                try:
                    async for chunk in llm.astream(normalize_messages_for_llm(runtime_messages)):
                        if accumulated_msg is None:
                            accumulated_msg = chunk
                        else:
                            accumulated_msg += chunk
                        if chunk.content:
                            if not content_emitted:
                                yield {"type": "log", "id": f"gen_s_{uuid.uuid4().hex[:8]}", "title": "✨ 开始生成回复", "status": "success"}
                            content_emitted = True
                            full_content += chunk.content
                            yield {"content": chunk.content}
                    stream_succeeded = True
                    break
                except Exception as stream_err:
                    logger.error(
                        f"[GeneralAgentRunner] Simple mode stream failed "
                        f"(attempt {stream_attempt + 1}/{MODEL_STREAM_MAX_RETRIES}): {stream_err}"
                    )
                    if (
                        stream_attempt < MODEL_STREAM_MAX_RETRIES - 1
                        and not content_emitted
                        and is_retryable_stream_error(stream_err)
                    ):
                        yield build_stream_retry_log(stream_err, stream_attempt)
                        await asyncio.sleep(2 ** stream_attempt)
                        continue
                    yield build_stream_error_log(stream_err)
                    return
            if not stream_succeeded:
                return
            
            tokens = extract_tokens_from_message(accumulated_msg)
            
            # Record final answer as a trace step
            self._increment_step()
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=self.config.model_name,
                temperature=self.config.temperature,
                tool_output={"content": full_content},
                raw_log=full_content,
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_synthesis)
            ))
            return

        # --- ReAct Mode (With Tools) ---
        from app.services.config_service import ConfigService
        max_steps_str = await ConfigService.get("agent_max_iterations")
        MAX_STEPS = int(max_steps_str) if max_steps_str else 5
        step = 0

        model_with_tools = await AgentConfigProvider.get_configured_llm(streaming=True, config=self.config)
        model_with_tools = model_with_tools.bind_tools(tools)

        from app.services.ai.memory_recall_policy import (
            MEMORY_SEARCH_CORRECTION_MSG,
            last_user_message_text,
            looks_like_cross_session_recall_query,
            tools_include_memory_search,
        )
        from app.services.memory_config_service import MemoryConfigService

        memory_search_available = tools_include_memory_search(tools)
        recall_query_pending = False
        if memory_search_available:
            try:
                enabled = await MemoryConfigService.get_bool("memory_service_enabled", True)
                recall_query_pending = enabled and looks_like_cross_session_recall_query(
                    last_user_message_text(history)
                )
            except Exception:
                recall_query_pending = looks_like_cross_session_recall_query(
                    last_user_message_text(history)
                )

        knowledge_search_pending = (
            self._requires_knowledge_search
            and tools_include_named(tools, "search_knowledge_base")
        )

        while step < MAX_STEPS:
            step += 1
            self._increment_step()
            
            # 3.1 Think
            start_thought = time.time()
            accumulated_content = ""
            accumulated_msg = None
            has_tool_call_indicator = False
            force_memory_recall = recall_query_pending and step == 1
            force_knowledge_search = knowledge_search_pending and step == 1
            
            yield {"type": "thinking", "status": "continuing"}
            
            user_content_emitted = False
            stream_succeeded = False
            for stream_attempt in range(MODEL_STREAM_MAX_RETRIES):
                accumulated_content = ""
                accumulated_msg = None
                has_tool_call_indicator = False
                try:
                    async for chunk in model_with_tools.astream(normalize_messages_for_llm(runtime_messages)):
                        if accumulated_msg is None:
                            accumulated_msg = chunk
                        else:
                            accumulated_msg += chunk

                        if chunk.content:
                            if "<function_calls" in (accumulated_content + chunk.content):
                                has_tool_call_indicator = True

                            # If it's a direct answer in Step 1, stream it (skip when recall query needs memory_search first)
                            if not has_tool_call_indicator and step == 1 and not force_memory_recall and not force_knowledge_search:
                                user_content_emitted = True
                                yield {"content": chunk.content}

                            accumulated_content += chunk.content
                    stream_succeeded = True
                    break
                except Exception as stream_err:
                    logger.error(
                        f"[GeneralAgentRunner] ReAct stream failed at step {step} "
                        f"(attempt {stream_attempt + 1}/{MODEL_STREAM_MAX_RETRIES}): {stream_err}"
                    )
                    if (
                        stream_attempt < MODEL_STREAM_MAX_RETRIES - 1
                        and not user_content_emitted
                        and is_retryable_stream_error(stream_err)
                    ):
                        yield build_stream_retry_log(stream_err, stream_attempt)
                        await asyncio.sleep(2 ** stream_attempt)
                        continue
                    yield build_stream_error_log(stream_err)
                    return

            if not stream_succeeded:
                return

            response = accumulated_msg
            tokens = extract_tokens_from_message(response)
            
            # --- [SPECIAL LOGIC: XML Tool Call Parsing] ---
            current_tool_calls = getattr(response, "tool_calls", [])
            if not current_tool_calls and accumulated_content and "<function_calls>" in accumulated_content:
                parsed_calls = parse_xml_tool_calls(accumulated_content)
                if parsed_calls:
                    current_tool_calls = parsed_calls

            # Trace Thought
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter,
                event_type="thought",
                agent_name=self.config.agent_name,
                model=self.config.model_name,
                temperature=self.config.temperature,
                tool_output={"content": accumulated_content, "tool_calls": [tc['name'] for tc in current_tool_calls]},
                raw_log=accumulated_content,
                execution_time_ms=(time.time() - start_thought) * 1000,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_thought)
            ))

            if not current_tool_calls:
                 if force_knowledge_search:
                     yield {
                         "type": "log",
                         "id": f"knowledge_search_intercept_{step}",
                         "title": "流程守护: 强制知识库检索",
                         "details": "本轮为知识库问答，须先调用 search_knowledge_base。",
                         "status": "warning",
                     }
                     runtime_messages.append(response)
                     append_system_instruction(runtime_messages, GeneralChatPrompts.KNOWLEDGE_SEARCH_CORRECTION_MSG)
                     knowledge_search_pending = False
                     continue

                 if force_memory_recall:
                     yield {
                         "type": "log",
                         "id": f"memory_recall_intercept_{step}",
                         "title": "流程守护: 强制跨会话记忆检索",
                         "details": "用户询问历史对话，须先调用 memory_search。",
                         "status": "warning",
                     }
                     runtime_messages.append(response)
                     append_system_instruction(runtime_messages, MEMORY_SEARCH_CORRECTION_MSG)
                     recall_query_pending = False
                     continue
                 
                 # Optimization: If we already streamed the answer in step 1, we are DONE.
                 if step == 1:
                     # 补充 synthesis 步骤供 Trace 展示；Token 已在上方 thought 步骤计入，此处勿重复
                     self._increment_step()
                     self.trace_buffer.append(AgentExecutionStep(
                         step_number=self.step_counter,
                         event_type="synthesis",
                         agent_name=self.config.agent_name,
                         model=self.config.model_name,
                         temperature=self.config.temperature,
                         tool_output={"content": accumulated_content},
                         raw_log=accumulated_content,
                         execution_time_ms=(time.time() - start_thought) * 1000,
                         prompt_tokens=0,
                         completion_tokens=0,
                         total_tokens=0,
                         timestamp=datetime.fromtimestamp(start_thought)
                     ))
                     return 
                 
                 # Break to enter Final Synthesis
                 break 
            
            # 3.2 Act (Process Tool Calls - Parallel)
            runtime_messages.append(response)
            
            pending_tasks = []
            for i, tool_call in enumerate(current_tool_calls):
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
                if tool_name == "search_knowledge_base":
                    knowledge_search_pending = False
                if not tool_call.get("id"): tool_call["id"] = f"call_{uuid.uuid4().hex[:8]}"
                
                yield {"type": "log", "id": tool_call['id'], "title": f"调用工具: {tool_name}", "details": f"参数: {json.dumps(tool_args, ensure_ascii=False)}", "status": "pending"}
                pending_tasks.append(self._execute_single_tool_safe(tool_call, tools, i))
            
            # Parallel execution with heartbeat
            tool_results = []
            if pending_tasks:
                execution_task = asyncio.ensure_future(asyncio.gather(*pending_tasks))
                waited_seconds = 0
                while not execution_task.done():
                    done, _ = await asyncio.wait([execution_task], timeout=1.5)
                    if execution_task in done: break
                    waited_seconds += 1.5
                    for tc in current_tool_calls:
                        yield {"type": "log", "id": tc['id'], "title": f"正在处理: {tc['name']} ({waited_seconds:.1f}s)", "status": "pending"}
                
                results = execution_task.result()
                for result in results:
                    tool_results.append(result)
                    if result.get("log"): yield result["log"]
                    if result.get("citation"): yield result["citation"]
                    if result.get("trace"): self.trace_buffer.append(result["trace"])
            
            tool_results.sort(key=lambda x: x["index"])
            for res in tool_results:
                if res.get("message"): runtime_messages.append(res["message"])
                self._increment_step()

        # --- Final Synthesis (After ReAct Loop) ---
        if step < MAX_STEPS:
            # Check if we have data to synthesize
            tool_msgs = [m for m in runtime_messages if isinstance(m, ToolMessage)]
            if not tool_msgs:
                # 🌟 兜底保障：若没有任何 ToolMessage，说明整个交互期间模型未能成功执行任何工具。
                # 此时，我们必须尝试提取大模型在 ReAct 循环中生成的最后一个 AIMessage 回答并输出给用户，
                # 避免整个会话因没有 ToolMessage 而被静默吞掉返回。若无回答，则给出一个友好的平台级报错提示。
                last_ai_msg = next((m for m in reversed(runtime_messages) if isinstance(m, AIMessage) and m.content), None)
                if last_ai_msg and last_ai_msg.content:
                    yield {"content": last_ai_msg.content}
                else:
                    yield {"content": "⚠️ 流程守护强力拦截成功，但系统未能成功执行或未挂载对应的 `search_knowledge_base` 知识库检索工具，大模型也未能给出有效的兜底纯文本回答。请联系系统管理员检查当前智能体的知识库配置或工具底座连接状态。"}
                return

            start_synthesis = time.time()
            yield {"type": "log", "id": f"synthesis_react_{uuid.uuid4().hex[:8]}", "title": "📝 汇总工具结果", "details": "已获取所需数据，正在组织语言...", "status": "success"}
            yield {"type": "thinking", "status": "continuing"}
            
            # --- [STRATEGY B+: Context-Aware Synthesis] ---
            synthesis_messages = []
            
            # 1. Add System Message
            synthesis_messages.append(runtime_messages[0])
            
            # 2. Add Clean History (User/Assistant pairs only, no ReAct noise)
            # We skip the very last human message as it will be merged with data below
            for msg in runtime_messages[1:-1]:
                if isinstance(msg, HumanMessage):
                    synthesis_messages.append(msg)
                elif isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
                    # Only keep final answers from previous turns
                    synthesis_messages.append(msg)
            
            # 3. Add Current Turn (Problem + Data)
            user_question = next((m.content for m in reversed(runtime_messages) if isinstance(m, HumanMessage)), "无原始问题")
            
            # [IMPROVED] Use structured trace instead of just raw tool outputs
            execution_review = self._format_trace_for_synthesis(self.trace_buffer)

            synthesis_messages.append(HumanMessage(content=GeneralChatPrompts.synthesis_user_message(user_question, execution_review)))
            
            final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
            
            content_emitted = False
            full_synthesis_content = ""
            accumulated_msg = None
            stream_succeeded = False
            for stream_attempt in range(MODEL_STREAM_MAX_RETRIES):
                accumulated_msg = None
                try:
                    async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
                        if accumulated_msg is None:
                            accumulated_msg = chunk
                        else:
                            accumulated_msg += chunk
                        if chunk.content:
                            if not content_emitted:
                                yield {"type": "log", "id": f"gen_start_{uuid.uuid4().hex[:8]}", "title": "✨ 开始生成回复", "status": "success"}
                            content_emitted = True
                            full_synthesis_content += chunk.content
                            yield {"content": chunk.content}
                    stream_succeeded = True
                    break
                except Exception as stream_err:
                    logger.error(
                        f"[GeneralAgentRunner] Synthesis stream failed "
                        f"(attempt {stream_attempt + 1}/{MODEL_STREAM_MAX_RETRIES}): {stream_err}"
                    )
                    if (
                        stream_attempt < MODEL_STREAM_MAX_RETRIES - 1
                        and not content_emitted
                        and is_retryable_stream_error(stream_err)
                    ):
                        yield build_stream_retry_log(stream_err, stream_attempt)
                        await asyncio.sleep(2 ** stream_attempt)
                        continue
                    yield build_stream_error_log(stream_err, title="⚠️ 总结生成失败")
                    return
            if not stream_succeeded:
                return

            tokens = extract_tokens_from_message(accumulated_msg)

            # Trace Synthesis
            self._increment_step()
            self.trace_buffer.append(AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name),
                temperature=self.config.synthesis_temperature or self.config.temperature,
                tool_output={"content": full_synthesis_content},
                raw_log=full_synthesis_content,
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                total_tokens=tokens["total_tokens"],
                timestamp=datetime.fromtimestamp(start_synthesis)
            ))

        if step >= MAX_STEPS:
             yield {"content": GeneralChatPrompts.MAX_STEPS_REACHED}

    async def _execute_single_tool_safe(self, tool_call: Dict, tools: List[Any], index: int) -> Dict[str, Any]:
        tool_name = tool_call["name"]; tool_args = tool_call["args"]; tool_id = tool_call["id"]
        start_tool = time.time(); tool_output = f"[TOOL_ERROR] Unknown tool: {tool_name}"
        
        target_tool = next((t for t in tools if t.name == tool_name), None)
        if target_tool:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    tool_output = await target_tool.ainvoke(tool_args)
                    break
                except (ConnectionError, TimeoutError) as e:
                    if attempt < max_retries - 1: await asyncio.sleep(2 ** attempt)
                    else: tool_output = f"Error executing {tool_name} after retries: {str(e)}"
                except Exception as e:
                    tool_output = f"Error executing {tool_name}: {str(e)}"; break
        
        duration_tool = (time.time() - start_tool) * 1000
        is_error = "Error" in str(tool_output)
        
        runtime_cfg = getattr(target_tool, "_runtime_config", None)
        t_model = getattr(runtime_cfg, "model_name", self.config.model_name)
        t_temp = getattr(runtime_cfg, "temperature", self.config.temperature)
        
        # Ensure types for AgentExecutionStep validation (especially when using Mocks in tests)
        if not isinstance(t_model, str): t_model = str(self.config.model_name)
        if not isinstance(t_temp, (int, float)): t_temp = float(self.config.temperature or 0)

        trace_step = AgentExecutionStep(
            step_number=self.step_counter, event_type="tool_call", agent_name=self.config.agent_name,
            model=t_model, temperature=t_temp, tool_name=tool_name, tool_input=tool_args,
            tool_output=tool_output if isinstance(tool_output, (dict, list)) else {"raw": str(tool_output)},
            execution_time_ms=duration_tool, status="success" if not is_error else "error",
            timestamp=datetime.fromtimestamp(start_tool)
        )
        
        log_event = {"type": "log", "id": tool_id, "title": f"工具完成: {tool_name} ({duration_tool:.0f}ms)", "details": str(tool_output)[:5000], "status": "success" if not is_error else "error", "model": t_model, "temperature": t_temp}
        
        # --- [NEW: Citation Extraction & Multi-Track Unpacking] ---
        citation_event = None
        final_tool_message_content = str(tool_output)

        if tool_name in ["search_knowledge_base", "jira_search"] and not is_error:
            try:
                # 1. Parse JSON Result
                parsed_res = None
                if isinstance(tool_output, str):
                    try:
                        parsed_res = json.loads(tool_output)
                    except:
                        pass
                elif isinstance(tool_output, dict):
                    parsed_res = tool_output

                if isinstance(parsed_res, dict):
                    # Unpack Multi-Track Format
                    # 'content' goes to LLM, 'citations' goes to Frontend
                    if "content" in parsed_res:
                        final_tool_message_content = parsed_res["content"]
                    
                    chunks = parsed_res.get("citations")
                    if isinstance(chunks, list) and len(chunks) > 0:
                        citation_event = {
                            "type": "citation",
                            "tool_call_id": tool_id,
                            "data": chunks
                        }
                elif isinstance(parsed_res, list) and len(parsed_res) > 0:
                    # Backward compatibility for direct array return
                    citation_event = {
                        "type": "citation",
                        "tool_call_id": tool_id,
                        "data": parsed_res
                    }
            except Exception as e:
                logger.warning(f"Failed to extract citations from {tool_name}: {e}")

        return {
            "index": index, 
            "message": ToolMessage(content=final_tool_message_content, tool_call_id=tool_id), 
            "trace": trace_step, 
            "log": log_event,
            "citation": citation_event
        }

    def _format_trace_for_synthesis(self, traces: List[AgentExecutionStep]) -> str:
        """
        Formats the execution trace into a readable summary for the synthesis model.
        """
        lines = ["【执行过程回顾】"]
        
        # Filter only relevant steps (thoughts and tool calls) from the current session
        # We assume traces are ordered.
        
        current_step_num = -1
        
        for trace in traces:
            # Skip router or init steps if any
            if trace.event_type not in ["thought", "tool_call"]:
                continue
            
            if trace.step_number != current_step_num:
                lines.append(f"\nStep {trace.step_number}:")
                current_step_num = trace.step_number
            
            if trace.event_type == "thought":
                # Extract a summary of thought if possible, or just raw
                thought_content = trace.raw_log or ""
                # Try to clean up XML tags if present
                thought_content = re.sub(r'<function_calls>.*?</function_calls>', '', thought_content, flags=re.DOTALL)
                thought_content = thought_content.strip()
                if thought_content:
                    lines.append(f"  [思考] {thought_content}")
            
            elif trace.event_type == "tool_call":
                status_icon = "✅" if trace.status == "success" else "❌"
                output_str = str(trace.tool_output)
                # Truncate very long outputs for context window
                if len(output_str) > 2000:
                    output_str = output_str[:2000] + "... (truncated)"
                
                lines.append(f"  [操作] {trace.tool_name}({json.dumps(trace.tool_input, ensure_ascii=False)})")
                lines.append(f"  [结果] {status_icon} {output_str}")

        return "\n".join(lines)
