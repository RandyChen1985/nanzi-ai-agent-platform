import logging
import json
import asyncio
import uuid
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncGenerator

from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.prompts import OpenClawPrompts
from app.services.ai.openclaw_client import OpenClawClient
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.executors.common import (
    MODEL_STREAM_MAX_RETRIES,
    build_stream_retry_log,
    is_retryable_stream_error,
)

logger = logging.getLogger(__name__)

class OpenClawExecutor(BaseExecutor):
    """
    Executor for OpenClaw (小龙虾) Engine interactions.
    """
    def __init__(
        self,
        agent_config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Optional[Dict[str, Any]] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        permission_options: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(agent_config, trace_id, trace_buffer, debug_options, user_info, conversation_id, permission_options)
        self.client = OpenClawClient()

    async def execute(self, history: List[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream chat completion from OpenClaw.
        """
        # Extract query from the last user message in history
        query = ""
        for msg in reversed(history):
            if msg["role"] == "user":
                query = msg["content"]
                break

        # Extract user name (Force English Account Name as per user request)
        username = "anonymous"
        if self.user_info:
            # We strictly use user_name (English account) and skip real_name (Chinese)
            username = self.user_info.get("user_name") or self.user_info.get("username") or "anonymous"

        # 1. Start Log
        yield {
            "type": "log",
            "id": f"claw_init_{uuid.uuid4().hex[:8]}",
            "title": "🧠 意图理解 (OpenClaw)",
            "details": f"正在向 OpenClaw 发送请求: '{query}' (User: {username})...",
            "status": "success"
        }

        # 1.1 Security Interception (Conditional)
        safety_enabled = self.config.engine_config.get("safety_check_enabled", True)
        if safety_enabled:
            security_log_id = f"claw_sec_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": security_log_id,
                "title": "🛡️ 安全合规性审计",
                "details": "正在利用系统模型检测输入内容是否违反安全准则...",
                "status": "pending"
            }

            custom_prompt = self.config.engine_config.get("safety_check_prompt")
            is_safe, reason = await self._verify_input_safety(query, custom_prompt)
            if not is_safe:
                yield {
                    "type": "log",
                    "id": security_log_id,
                    "title": "❌ 内容安全拦截",
                    "details": f"输入内容未通过安全审查: {reason}",
                    "status": "error"
                }
                yield {"content": f"\n\n> ⚠️ **安全警告**: 您的输入未能通过系统安全审计（{reason}）。请求已拦截。"}
                return

            yield {
                "type": "log",
                "id": security_log_id,
                "title": "✅ 审计通过",
                "details": "内容符合安全合规要求，正在继续执行...",
                "status": "success"
            }
        
        # 2. Reasoning Log
        reasoning_log_id = f"claw_reason_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": reasoning_log_id,
            "title": "🦞 小龙虾正在思考",
            "details": "正在利用 OpenClaw 进行深度推理与搜索...",
            "status": "pending"
        }
        
        start_synthesis = time.time()
        content_emitted = False
        full_content = ""

        for attempt in range(MODEL_STREAM_MAX_RETRIES):
            try:
                # Determine session key for OpenClaw (Prefer Conversation ID for session persistence)
                session_key = self.conversation_id or self.trace_id

                datasets: list[dict[str, Any]] = []
                if self.user_info:
                    raw_uid = self.user_info.get("user_id") or self.user_info.get("id")
                    if raw_uid is not None:
                        try:
                            from app.core.orm import AsyncSessionLocal
                            from app.services.permission_service import PermissionService

                            uid_int = int(raw_uid)
                            async with AsyncSessionLocal() as db:
                                datasets = await PermissionService(db).get_accessible_ragflow_meta_datasets(uid_int)
                        except Exception as e:
                            logger.warning(
                                "OpenClaw AUTH_CONTEXT datasets resolve failed: %s", e,
                                exc_info=True,
                            )

                async for chunk in self.client.chat_stream(
                    query=query,
                    conversation_id=session_key,
                    history=history,
                    config=self.config.engine_config,
                    user=username,
                    user_info=self.user_info,
                    datasets=datasets,
                ):
                    if chunk.get("type") == "answer":
                        if not content_emitted:
                            # Update Log to Success
                            yield {
                                "type": "log",
                                "id": reasoning_log_id,
                                "title": "✅ 思考完成",
                                "details": f"OpenClaw 已准备好回答...",
                                "status": "success"
                            }

                            ttft = time.time() - start_synthesis
                            yield {
                                "type": "log",
                                "id": f"claw_gen_{uuid.uuid4().hex[:8]}",
                                "title": f"✨ 开始生成回复 ({ttft:.2f}s)",
                                "details": f"首字延迟 (TTFT): {ttft:.2f}s，OpenClaw 正在输出回复内容...",
                                "status": "success"
                            }
                            content_emitted = True

                        content = chunk.get("content", "")
                        full_content += content
                        yield {"content": content}

                    elif chunk.get("type") == "error":
                        yield {
                            "type": "log",
                            "id": reasoning_log_id,
                            "title": "❌ OpenClaw 调用失败",
                            "details": chunk.get("content", "未知错误"),
                            "status": "error"
                        }
                        yield {"content": f"\n\n> ⚠️ **OpenClaw 服务异常**: {chunk.get('content')}"}
                        return

                # --- Output Safety Audit (Post-Retraction Logic) ---
                if safety_enabled and full_content.strip():
                    # Auditing for AI output content
                    base_output_prompt = OpenClawPrompts.OUTPUT_SAFETY_AUDIT

                    # Handle custom output prompt strategy
                    custom_output_prompt = self.config.engine_config.get("safety_check_output_prompt")
                    output_strategy = self.config.engine_config.get("safety_check_output_strategy", "append")

                    if custom_output_prompt and custom_output_prompt.strip():
                        if output_strategy == "override":
                            output_audit_prompt = custom_output_prompt
                        else:
                            output_audit_prompt = f"{base_output_prompt}\n\n【用户自定义补充规则】\n{custom_output_prompt}"
                    else:
                        output_audit_prompt = base_output_prompt

                    is_safe_output, reason_output = await self._verify_input_safety(full_content, output_audit_prompt)

                    if not is_safe_output:
                        logger.warning(f"[Security] Retracted unsafe output for agent {self.config.agent_name}. Reason: {reason_output}")
                        yield {
                            "type": "retraction",
                            "content": f"\n\n> ⚠️ **安全警告**: 该回答由于包含敏感或违规信息（{reason_output}），已被系统自动撤回并拦截。",
                            "reason": reason_output
                        }
                        full_content = f"[RETRACTED DUE TO: {reason_output}]"
                break

            except Exception as e:
                if content_emitted:
                    logger.exception(f"OpenClaw execution error after output started: {str(e)}")
                    yield {
                        "type": "log",
                        "id": reasoning_log_id,
                        "title": "❌ 系统执行异常",
                        "details": str(e),
                        "status": "error"
                    }
                    break

                logger.warning(
                    f"[OpenClawRetry] Stream failed before output "
                    f"(attempt {attempt + 1}/{MODEL_STREAM_MAX_RETRIES}): {e}"
                )
                if is_retryable_stream_error(e) and attempt < MODEL_STREAM_MAX_RETRIES - 1:
                    yield build_stream_retry_log(e, attempt)
                    await asyncio.sleep(2 ** attempt)
                    continue

                logger.exception(f"OpenClaw execution error: {str(e)}")
                yield {
                    "type": "log",
                    "id": reasoning_log_id,
                    "title": "❌ 系统执行异常",
                    "details": str(e),
                    "status": "error"
                }
                break

        # Final Trace Step
        execution_time = (time.time() - start_synthesis) * 1000
        self._increment_step()
        self.trace_buffer.append(AgentExecutionStep(
            step_number=self.step_counter,
            event_type="synthesis",
            agent_name=self.config.agent_name,
            model="openclaw-v1",
            tool_output={"content": full_content},
            raw_log=full_content,
            execution_time_ms=execution_time,
            timestamp=datetime.now()
        ))

    async def _verify_input_safety(self, query: str, custom_prompt: Optional[str] = None) -> (bool, str):
        """
        Use system default LLM to verify if the user input is safe.
        Returns: (is_safe, reason)
        """
        try:
            # 1. Get System Model
            llm = await get_llm_async(temperature=0.0)
            if not llm:
                logger.warning("[Security] System LLM for safety check is unavailable. Skipping.")
                return True, ""

            # 2. Prepare Audit Prompt
            base_prompt = OpenClawPrompts.INPUT_SAFETY_AUDIT
            
            # Handle input safety check strategy
            if not custom_prompt:
                input_prompt = self.config.engine_config.get("safety_check_input_prompt")
                input_strategy = self.config.engine_config.get("safety_check_input_strategy", "append")
                
                if input_prompt and input_prompt.strip():
                    if input_strategy == "override":
                        system_prompt = input_prompt
                    else:
                        system_prompt = f"{base_prompt}\n\n【用户自定义补充规则】\n{input_prompt.strip()}"
                else:
                    system_prompt = base_prompt
            else:
                system_prompt = custom_prompt

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"用户输入：{query}")
            ]

            # 3. Invoke LLM
            response = await llm.ainvoke(messages)
            result = str(response.content).strip().upper()

            if "UNSAFE" in result:
                reason = result.replace("UNSAFE", "").strip(" ()") or "违反内容合规要求"
                logger.warning(f"[Security] Intercepted unsafe input: {query} -> Reason: {reason}")
                return False, reason

            return True, ""
        except Exception as e:
            logger.error(f"[Security] Safety check encountered an error: {e}")
            # Fallback to safe to avoid blocking legitimate users in case of service glitch
            return True, ""
