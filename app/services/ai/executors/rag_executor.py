import logging
import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncGenerator

from app.services.ai.executors.base import BaseExecutor
from app.services.ai.ragflow_client import RagFlowClient
from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.executors.common import (
    MODEL_STREAM_MAX_RETRIES,
    build_stream_retry_log,
    is_retryable_stream_error,
)

logger = logging.getLogger(__name__)

class RAGExecutor(BaseExecutor):
    """
    Executor for RAGFlow Engine interactions.
    Now inherits from BaseExecutor for proper trace logging.
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
        self.client = RagFlowClient()

    async def execute(self, history: List[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream chat completion from RAGFlow.
        """
        import time
        full_content = ""
        # Extract query from the last user message in history
        query = ""
        for msg in reversed(history):
            if msg["role"] == "user":
                query = msg["content"]
                break

        # Trace Step: Start Retrieval
        self._increment_step()
        start_time = time.time()

        # 1. Intent Analysis Log
        yield {
            "type": "log",
            "id": f"intent_{uuid.uuid4().hex[:8]}",
            "title": "🧠 语义理解",
            "details": f"正在分析用户意图并提取关键词: '{query}'...",
            "status": "success"
        }
        await asyncio.sleep(0.5) # Slight pause for better UX

        # 2. Retrieval Log
        synthesis_log_id = f"synthesis_rag_{uuid.uuid4().hex[:8]}"
        yield {
            "type": "log",
            "id": synthesis_log_id,
            "title": "🔍 知识库检索",
            "details": "正在从向量数据库中匹配相关文档片段...",
            "status": "pending"
        }
        
        start_synthesis = time.time()
        max_retries = MODEL_STREAM_MAX_RETRIES
        for attempt in range(max_retries):
            try:
                content_emitted = False
                async for chunk in self.client.chat_stream(
                    query=query,
                    conversation_id=self.conversation_id or self.trace_id,
                    history=history,
                    config=self.config.engine_config
                ):
                    if chunk.get("type") == "answer":
                        if not content_emitted:
                            # Update Retrieval Log to Success
                            yield {
                                "type": "log",
                                "id": synthesis_log_id,
                                "title": "✅ 检索完成",
                                "details": f"已找到相关知识点，正在组织语言解答...",
                                "status": "success"
                            }
                            
                            ttft = time.time() - start_synthesis
                            yield {
                                "type": "log",
                                "id": f"gen_start_{uuid.uuid4().hex[:8]}",
                                "title": f"✨ 开始生成回复 ({ttft:.2f}s)",
                                "details": f"首字延迟 (TTFT): {ttft:.2f}s，知识库回答正在输出...",
                                "status": "success"
                            }
                        content_emitted = True
                        yield {"content": chunk["content"]}
                        full_content += chunk["content"]
                    elif chunk.get("type") == "citation":
                        # 1. Record Citations in Permanent Trace
                        self._increment_step()
                        self.trace_buffer.append(AgentExecutionStep(
                            step_number=self.step_counter,
                            event_type="tool_call",
                            agent_name=self.config.agent_name,
                            model="RAGFlow-Remote",
                            temperature=self.config.temperature,
                            tool_name="rag_retrieval",
                            tool_output={"citations": chunk["data"]},
                            status="success",
                            timestamp=datetime.now()
                        ))

                        # 2. Yield Standard Citation Event for UI Rendering
                        yield {
                            "type": "citation",
                            "data": chunk["data"]
                        }

                        # 3. Yield a simple log for thinking process visibility
                        yield {
                                "type": "log",
                                "id": f"citation_log_{uuid.uuid4().hex[:8]}",
                                "title": "📚 关联知识库原文",
                                "details": f"已成功匹配 {len(chunk['data'])} 条相关文档片段",
                                "status": "success"
                        }
                    elif chunk.get("type") == "error":
                            yield {"content": f"\n\n[RAGFlow Error] {chunk['content']}"}
                
                # Trace Conclusion
                self.trace_buffer.append(AgentExecutionStep(
                    step_number=self.step_counter,
                    event_type="thought",
                    agent_name=self.config.agent_name,
                    model="RAGFlow-Remote",
                    temperature=self.config.temperature,
                    tool_output={"content": full_content},
                    execution_time_ms=(time.time() - start_time) * 1000,
                    status="success",
                    timestamp=datetime.fromtimestamp(start_time)
                ))
                
                # If we finish the stream successfully, break the retry loop
                break
            
            except Exception as e:
                # If we have already yielded content, we cannot simply retry as it would duplicate output
                if full_content:
                    logger.error(f"RAGFlow Stream Failed mid-stream: {e}", exc_info=True)
                    yield {"content": f"\n\n[系统错误] 知识库连接中断: {str(e)}", "status": "error"}
                    break
                
                # If we haven't yielded anything yet, we can retry safe-ish
                if is_retryable_stream_error(e) and attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"[RAGRetry] Stream failed before output "
                        f"(attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s... Error: {e}"
                    )
                    yield build_stream_retry_log(e, attempt)
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"RAGFlow Execution Failed after {attempt+1} attempts: {e}", exc_info=True)
                    yield {"content": f"\n\n[系统错误] RAGFlow 引擎执行失败: {str(e)}", "status": "error"}
                    break
