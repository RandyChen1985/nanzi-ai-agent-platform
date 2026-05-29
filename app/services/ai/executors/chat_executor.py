import time
import uuid
import json
import logging
import asyncio
import re
from typing import List, Dict, Any, AsyncGenerator, Optional
from datetime import datetime

from langchain_core.messages import (
    HumanMessage, 
    AIMessage, 
    SystemMessage, 
    BaseMessage,
    ToolMessage
)
from app.schemas.agent import ChatConfig, AgentExecutionStep
from app.services.ai.tools.registry import ToolRegistry
from app.services.ai.config import AgentConfigProvider

logger = logging.getLogger(__name__)

def _extract_tokens_from_message(msg: Any) -> dict:
    """
    Extract token usage from LangChain message or chunk.
    """
    res = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not msg:
        return res
    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
        um = msg.usage_metadata
        res["prompt_tokens"] = um.get("input_tokens") or 0
        res["completion_tokens"] = um.get("output_tokens") or 0
        res["total_tokens"] = um.get("total_tokens") or (res["prompt_tokens"] + res["completion_tokens"])
        return res
    if hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
        tu = msg.response_metadata.get("token_usage")
        if isinstance(tu, dict):
            res["prompt_tokens"] = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            res["completion_tokens"] = tu.get("completion_tokens") or tu.get("output_tokens") or 0
            res["total_tokens"] = tu.get("total_tokens") or (res["prompt_tokens"] + res["completion_tokens"])
            return res
    return res

class GeneralChatExecutor:
    def __init__(self, config: ChatConfig, trace_id: str, trace_buffer: List[AgentExecutionStep], debug_options: Dict[str, Any] = {}, user_info: Optional[Dict[str, Any]] = None, conversation_id: Optional[str] = None):
        self.config = config
        self.trace_id = trace_id
        self.trace_buffer = trace_buffer
        self.debug_options = debug_options
        self.user_info = user_info
        self.conversation_id = conversation_id
        self.step_counter = 0

    def _increment_step(self):
        self.step_counter += 1

    async def execute(
        self,
        history: List[Dict[str, str]]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # 1. Prepare LLM
        configured_tools = self.config.tools or []
        tools = []
        if configured_tools:
            tools = await ToolRegistry.get_tools(configured_tools)

        system_tools = ToolRegistry.get_system_implicit_tools()
        if system_tools:
            tools.extend(system_tools)

        # 2. Build Messages
        langchain_messages = []
        system_content = self.config.system_prompt
        langchain_messages.append(SystemMessage(content=system_content))

        for m in history:
            role = m["role"]
            content = m["content"]
            if role == "user":
                import base64
                import os
                files = m.get("files")
                img_extensions = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                img_files = []
                non_img_files = []
                if files:
                    for f in files:
                        if f.get("type") in ("skill", "knowledge_base"):
                            continue
                        ext = f.get("ext", "")
                        if not ext and f.get("url"):
                            ext = os.path.splitext(f["url"])[1]
                        if ext and ext.lower() in img_extensions:
                            img_files.append(f)
                        else:
                            non_img_files.append(f)
                
                # 构造非图片/Skills 的路径附随与引导提示词
                attachment_prompt = ""
                if non_img_files:
                    lines = ["\n\n【用户随随上传了非图片附件信息】："]
                    for f in non_img_files:
                        url = f.get("url", "")
                        filename = f.get("filename", "未知文件")
                        size_str = f"{(f.get('size', 0) / 1024):.1f} KB" if f.get("size") else "未知大小"
                        unique_name = os.path.basename(url)
                        abs_path = f"/app/data/uploads/{unique_name}"
                        lines.append(f"- 文件名: {filename} (大小: {size_str})")
                        lines.append(f"  服务器内绝对路径: {abs_path}")
                    lines.append("[系统提示: 以上非图片文件或 skills 配置已安全保存在服务器。如果您拥有相关的读取文件工具、数据分析工具、数据库工具或 Python 代码解释器工具，可以直接使用上述绝对路径读取文件内容并为用户进行深度分析。]")
                    attachment_prompt = "\n".join(lines)
                
                final_text = content + attachment_prompt
                
                if img_files:
                    multimodal_content = [{"type": "text", "text": final_text}]
                    for f in img_files:
                        url = f.get("url", "")
                        base64_data = None
                        if url.startswith("/static/uploads/"):
                            filename = os.path.basename(url)
                            local_path = os.path.join("data/uploads", filename)
                            if os.path.exists(local_path):
                                try:
                                    with open(local_path, "rb") as image_file:
                                        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
                                    ext_cleaned = f.get("ext", "png").lower().strip(".")
                                    if ext_cleaned == "jpg": ext_cleaned = "jpeg"
                                    mime_type = f"image/{ext_cleaned}"
                                    base64_data = f"data:{mime_type};base64,{encoded_string}"
                                except Exception as e:
                                    logger.warning(f"Failed to read local image for vision: {e}")
                        img_url = base64_data if base64_data else url
                        if img_url:
                            multimodal_content.append({
                                "type": "image_url",
                                "image_url": {"url": img_url}
                            })
                    langchain_messages.append(HumanMessage(content=multimodal_content))
                else:
                    langchain_messages.append(HumanMessage(content=final_text))
            elif role == "assistant":
                langchain_messages.append(AIMessage(content=content))
            elif role == "system":
                # Crucial: Preserving injected identity and guidelines
                langchain_messages.append(SystemMessage(content=content))

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
            async for chunk in llm.astream(langchain_messages):
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
            
            tokens = _extract_tokens_from_message(accumulated_msg)
            
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

        while step < MAX_STEPS:
            step += 1
            self._increment_step()
            
            # 3.1 Think
            start_thought = time.time()
            accumulated_content = ""
            accumulated_msg = None
            has_tool_call_indicator = False
            force_memory_recall = recall_query_pending and step == 1
            
            yield {"type": "thinking", "status": "continuing"}
            
            # Turn 1: Stream to keep latency low. Following turns: accumulate for tool call detection.
            async for chunk in model_with_tools.astream(langchain_messages):
                if accumulated_msg is None:
                    accumulated_msg = chunk
                else:
                    accumulated_msg += chunk
                
                if chunk.content:
                    if "<function_calls" in (accumulated_content + chunk.content):
                        has_tool_call_indicator = True
                    
                    # If it's a direct answer in Step 1, stream it (skip when recall query needs memory_search first)
                    if not has_tool_call_indicator and step == 1 and not force_memory_recall:
                        yield {"content": chunk.content}
                    
                    accumulated_content += chunk.content

            response = accumulated_msg
            tokens = _extract_tokens_from_message(response)
            
            # --- [SPECIAL LOGIC: XML Tool Call Parsing] ---
            current_tool_calls = getattr(response, "tool_calls", [])
            if not current_tool_calls and accumulated_content and "<function_calls>" in accumulated_content:
                parsed_calls = self._parse_xml_tool_calls(accumulated_content)
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
                 if force_memory_recall:
                     yield {
                         "type": "log",
                         "id": f"memory_recall_intercept_{step}",
                         "title": "流程守护: 强制跨会话记忆检索",
                         "details": "用户询问历史对话，须先调用 memory_search。",
                         "status": "warning",
                     }
                     langchain_messages.append(response)
                     langchain_messages.append(SystemMessage(content=MEMORY_SEARCH_CORRECTION_MSG))
                     recall_query_pending = False
                     continue
                 
                 # Optimization: If we already streamed the answer in step 1, we are DONE.
                 if step == 1:
                     # Record final answer as a trace step even in step 1 exit
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
                         prompt_tokens=tokens["prompt_tokens"],
                         completion_tokens=tokens["completion_tokens"],
                         total_tokens=tokens["total_tokens"],
                         timestamp=datetime.fromtimestamp(start_thought)
                     ))
                     return 
                 
                 # Break to enter Final Synthesis
                 break 
            
            # 3.2 Act (Process Tool Calls - Parallel)
            langchain_messages.append(response)
            
            pending_tasks = []
            for i, tool_call in enumerate(current_tool_calls):
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]
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
                if res.get("message"): langchain_messages.append(res["message"])
                self._increment_step()

        # --- Final Synthesis (After ReAct Loop) ---
        if step < MAX_STEPS:
            # Check if we have data to synthesize
            tool_msgs = [m for m in langchain_messages if isinstance(m, ToolMessage)]
            if not tool_msgs:
                return

            start_synthesis = time.time()
            yield {"type": "log", "id": f"synthesis_react_{uuid.uuid4().hex[:8]}", "title": "📝 汇总工具结果", "details": "已获取所需数据，正在组织语言...", "status": "success"}
            yield {"type": "thinking", "status": "continuing"}
            
            # --- [STRATEGY B+: Context-Aware Synthesis] ---
            synthesis_messages = []
            
            # 1. Add System Message
            synthesis_messages.append(langchain_messages[0])
            
            # 2. Add Clean History (User/Assistant pairs only, no ReAct noise)
            # We skip the very last human message as it will be merged with data below
            for msg in langchain_messages[1:-1]:
                if isinstance(msg, HumanMessage):
                    synthesis_messages.append(msg)
                elif isinstance(msg, AIMessage) and not msg.tool_calls and msg.content:
                    # Only keep final answers from previous turns
                    synthesis_messages.append(msg)
            
            # 3. Add Current Turn (Problem + Data)
            user_question = next((m.content for m in reversed(langchain_messages) if isinstance(m, HumanMessage)), "无原始问题")
            
            # [IMPROVED] Use structured trace instead of just raw tool outputs
            execution_review = self._format_trace_for_synthesis(self.trace_buffer)

            synthesis_messages.append(HumanMessage(content=(
                f"【当前追问】：{user_question}\n\n"
                f"{execution_review}\n\n"
                "请结合上述【执行过程回顾】和最新结果，为用户提供准确、连贯的最终回答。\n"
                "注：如果执行过程主要是执行了一个外部动作（如发送钉钉消息、创建任务等），请直接简洁地告知执行结果即可，无需重复发送的具体内容或进行冗长的总结。"
            )))
            
            final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
            
            content_emitted = False
            full_synthesis_content = ""
            accumulated_msg = None
            async for chunk in final_llm.astream(synthesis_messages):
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

            tokens = _extract_tokens_from_message(accumulated_msg)

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
             yield {"content": "[系统提示] 达到最大执行步骤，停止执行。"}

    async def _execute_single_tool_safe(self, tool_call: Dict, tools: List[Any], index: int) -> Dict[str, Any]:
        from langchain_core.messages import ToolMessage
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

    def _parse_xml_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        tool_calls = []
        match = re.search(r"<function_calls>(.*?)</function_calls>", content, re.DOTALL | re.IGNORECASE)
        if not match: match = re.search(r"<function_calls>(.*)", content, re.DOTALL | re.IGNORECASE)
        if not match: return tool_calls
        xml_content = match.group(0)
        try:
            from xml.etree import ElementTree as ET
            fixed_xml = xml_content.replace("</invokefunction_calls>", "</invoke></function_calls>")
            if not fixed_xml.endswith("</function_calls>"): fixed_xml += "</function_calls>"
            root = ET.fromstring(fixed_xml)
            for invoke in root.findall("invoke"):
                name = invoke.get("name")
                args = {}
                for param in invoke.findall("parameter"):
                    p_name = param.get("name")
                    p_value = param.text
                    if p_name: args[p_name] = p_value
                if name: tool_calls.append({"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"})
        except:
            pass
        return tool_calls