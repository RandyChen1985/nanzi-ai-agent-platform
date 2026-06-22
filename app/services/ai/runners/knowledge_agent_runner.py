"""KnowledgeAgentRunner：知识库问答专用执行链路。"""
from __future__ import annotations

import inspect
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.common import (
    convert_history_to_messages,
    normalize_messages_for_llm,
    tools_include_named,
)
from app.services.ai.executors.prompts import KnowledgeChatPrompts
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.metadata_rag_service import MetadataRagService
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, runtime_tool_spec_from_legacy_tool
from app.services.ai.knowledge_utils import (
    NO_KNOWLEDGE_DATASET_MESSAGE,
    collect_citation_ids_from_payload,
    filter_invalid_citation_markers,
    format_dataset_ids_for_tool,
    has_knowledge_citation_markers,
    knowledge_prefetch_had_citations,
    resolve_knowledge_dataset_ids,
    text_has_valid_citation_markers,
)
from app.services.ai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class KnowledgeAgentRunner(AssistantAgentRunner):
    """知识库问答 Runner：自动检索 + AgentScope ReAct，可扩展挂载业务工具。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._valid_citation_ids: Set[str] = set()
        self._rag_empty = False

    def _is_hallucinated_knowledge_reply(self, text: str) -> bool:
        text_clean = text.strip()
        if not text_clean:
            return False
        # 1. 包含表格结构视为事实性回答
        if "|" in text_clean and "---" in text_clean:
            return True
        # 2. 较长且包含列表项标记，视为详细步骤脑补
        if len(text_clean) > 120 and ("-" in text_clean or "1." in text_clean):
            return True
        # 3. 较长且无任何拒绝/无法找到相关词汇的陈述，视为编造
        refusal_keywords = ["未找到", "没有找到", "暂无", "无法", "抱歉", "没有", "换个关键词", "换关键词", "不具备", "不能"]
        has_refusal = any(kw in text_clean for kw in refusal_keywords)
        if len(text_clean) > 100 and not has_refusal:
            return True
        return False

    def _is_hallucinated_with_rag_reply(self, text: str) -> bool:
        text_clean = text.strip()
        if not text_clean:
            return False
        # 1. 检查是否存在引用标记（规范 [ID:n] 或旧式 [n]）
        if self._valid_citation_ids:
            if text_has_valid_citation_markers(text_clean, self._valid_citation_ids):
                return False
        elif has_knowledge_citation_markers(text_clean):
            return False
        # 2. 如果没有任何引用，且是偏事实性描述或表格/列表，且没有拒绝词，判定为幻觉
        refusal_keywords = ["未找到", "没有找到", "暂无", "无法", "抱歉", "没有", "换个关键词", "换关键词", "不具备", "不能", "未提及", "未在"]
        has_refusal = any(kw in text_clean for kw in refusal_keywords)

        is_factual = False
        if "|" in text_clean and "---" in text_clean:
            is_factual = True
        elif len(text_clean) > 100 and ("-" in text_clean or "1." in text_clean):
            is_factual = True
        elif len(text_clean) > 120:
            is_factual = True

        if is_factual and not has_refusal:
            return True
        return False

    def _build_synthesis_user_message(self, user_query: str, execution_review: str) -> str:
        return KnowledgeChatPrompts.synthesis_user_message(user_query, execution_review)

    def _runtime_agent_name(self) -> str:
        return self.config.agent_name or "KnowledgeAgent"

    async def _resolve_knowledge_tools(self) -> List[RuntimeToolSpec]:
        configured_tools = self.config.tools or []
        tools: List[RuntimeToolSpec] = []
        if configured_tools:
            tools = await ToolRegistry.get_runtime_tools(configured_tools)

        system_tools = ToolRegistry.get_system_implicit_tools()
        if system_tools:
            tools.extend(
                runtime_tool_spec_from_legacy_tool(tool, source_type="system")
                for tool in system_tools
            )

        if not tools_include_named(tools, "search_knowledge_base"):
            kb_tool = await ToolRegistry.get_runtime_tool("search_knowledge_base")
            if kb_tool:
                tools.append(kb_tool)
        return tools

    async def _auto_invoke_search_knowledge_base(
        self,
        *,
        query: str,
        tools: List[RuntimeToolSpec],
        dataset_ids: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """ReAct 开始前平台侧自动执行 search_knowledge_base。"""
        kb_spec = next((tool for tool in tools if tool.name == "search_knowledge_base"), None)
        if kb_spec is None:
            logger.warning("[KnowledgeAgentRunner] search_knowledge_base tool missing; skip auto prefetch")
            return

        tool_id = f"kb_prefetch_{uuid.uuid4().hex[:8]}"
        started_at = time.time()
        yield {
            "type": "log",
            "id": tool_id,
            "title": "自动检索知识库",
            "details": (
                f"平台自动调用 search_knowledge_base("
                f"query={query or 'None'}, dataset_ids={dataset_ids or 'agent-default'})"
            ),
            "status": "pending",
            "category": "tool",
            "started_at": int(started_at * 1000),
        }

        output = ""
        try:
            tool_kwargs: Dict[str, Any] = {"query": query or ""}
            if dataset_ids:
                tool_kwargs["dataset_ids"] = dataset_ids
            result = kb_spec.callable(**tool_kwargs)
            if inspect.isawaitable(result):
                result = await result
            output = str(result or "")
            self._valid_citation_ids = collect_citation_ids_from_payload(output)
        except Exception as exc:
            logger.error("[KnowledgeAgentRunner] Auto search_knowledge_base failed: %s", exc)
            err_msg = str(exc)
            if MetadataRagService._is_service_unavailable(err_msg):
                output = MetadataRagService.knowledge_unavailable_hint(err_msg)
            else:
                output = f"[TOOL_ERROR] 自动检索知识库失败: {exc}"

        duration_ms = (time.time() - started_at) * 1000
        self._increment_step()
        observation = self._build_tool_observation(
            tool_id=tool_id,
            tool_name="search_knowledge_base",
            tool_args={"query": query, "dataset_ids": dataset_ids},
            tool_output=output,
            duration_tool=duration_ms,
            target_tool=kb_spec,
            tool_index=0,
        )
        if observation.get("trace"):
            self.trace_buffer.append(observation["trace"])
        service_unavailable = self._is_knowledge_service_unavailable(output)
        yield {
            "type": "log",
            "id": tool_id,
            "title": "工具完成: search_knowledge_base",
            "details": observation.get("log", {}).get("details", output[:500]),
            "status": "error" if service_unavailable else "success",
            "category": "tool",
            "execution_time_ms": duration_ms,
        }
        if observation.get("citation"):
            yield observation["citation"]
        yield {
            "__knowledge_output__": observation.get("final_tool_message_content") or output,
            "__knowledge_service_unavailable__": service_unavailable,
        }

    @staticmethod
    def _is_knowledge_service_unavailable(tool_output: Any) -> bool:
        """仅识别工具显式返回的错误标记，勿对检索正文做子串匹配（正文可能含 503/timeout 等）。"""
        text = str(tool_output or "")
        if "[知识库服务不可用]" in text:
            return True
        normalized = text.lstrip()
        if normalized.startswith("[Tool Error]") or normalized.startswith("[TOOL_ERROR]"):
            return MetadataRagService._is_service_unavailable(text)
        return False

    async def _yield_knowledge_no_dataset_abort(
        self,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "type": "log",
            "id": f"kb_no_dataset_{uuid.uuid4().hex[:8]}",
            "title": "未指定知识库",
            "details": NO_KNOWLEDGE_DATASET_MESSAGE,
            "status": "error",
            "category": "knowledge",
        }
        yield {
            "content": KnowledgeChatPrompts.KNOWLEDGE_NO_DATASET_CONTENT,
            "status": "error",
        }

    async def _yield_knowledge_fatal_abort(
        self,
        details: Any = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "type": "log",
            "id": f"kb_fatal_{uuid.uuid4().hex[:8]}",
            "title": "知识库服务不可用",
            "details": truncate_for_context(str(details or ""), max_len=1000) or "知识库服务不可用",
            "status": "error",
        }
        yield {
            "content": KnowledgeChatPrompts.KNOWLEDGE_SERVICE_UNAVAILABLE_CONTENT,
            "status": "error",
        }

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
        async with TraceSpanContext(
            trace_buffer=self.trace_buffer,
            event_type="agent_execution",
            span_name="KnowledgeAgentRunner",
        ):
            async for chunk in self._execute_raw(history):
                yield chunk

    async def _execute_raw(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.multimodal_support import (
            ensure_multimodal_compatible,
            resolve_runtime_model_name,
        )
        from app.services.config_service import ConfigService

        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {"content": incompatible_msg, "status": "error"}
            return

        tools = await self._resolve_knowledge_tools()
        if not tools_include_named(tools, "search_knowledge_base"):
            yield {
                "type": "error",
                "status": "error",
                "content": "知识库工具 search_knowledge_base 不可用，无法执行知识库问答。",
            }
            return

        user_question = next(
            (str(message.get("content") or "") for message in reversed(history) if message.get("role") == "user"),
            "",
        ).strip()

        system_content = self.config.system_prompt or ""
        system_content = f"{KnowledgeChatPrompts.TURN_SYSTEM_HINT}\n\n{system_content}"

        resolved_dataset_ids, dataset_error = await resolve_knowledge_dataset_ids(
            query=user_question,
        )
        if dataset_error and not resolved_dataset_ids:
            async for chunk in self._yield_knowledge_no_dataset_abort():
                yield chunk
            return

        prefetch_dataset_ids = format_dataset_ids_for_tool(resolved_dataset_ids)
        prefetched_knowledge_output: str | None = None
        knowledge_service_unavailable = False
        prefetch_had_citations = False
        async for chunk in self._auto_invoke_search_knowledge_base(
            query=user_question,
            tools=tools,
            dataset_ids=prefetch_dataset_ids,
        ):
            if chunk.get("__knowledge_service_unavailable__"):
                knowledge_service_unavailable = True
            if chunk.get("__knowledge_output__") is not None:
                prefetched_knowledge_output = str(chunk["__knowledge_output__"])
                prefetch_had_citations = knowledge_prefetch_had_citations(prefetched_knowledge_output)
                continue
            yield chunk

        if knowledge_service_unavailable:
            async for chunk in self._yield_knowledge_fatal_abort(prefetched_knowledge_output):
                yield chunk
            return

        self._rag_empty = False
        if prefetched_knowledge_output:
            try:
                import json
                parsed = json.loads(prefetched_knowledge_output)
                if isinstance(parsed, dict) and parsed.get("status") == "empty":
                    self._rag_empty = True
            except Exception:
                pass
            system_content += (
                "\n\n"
                + KnowledgeChatPrompts.prefetched_knowledge_context(
                    user_question,
                    prefetched_knowledge_output,
                )
            )

        from app.services.ai.runtime.agentscope.workspace import (
            append_session_workspace_sandbox_to_system_prompt,
        )

        system_content = await append_session_workspace_sandbox_to_system_prompt(
            system_content,
            user_id=self._runtime_user_id(),
            conversation_id=self.conversation_id,
            tools=tools,
        )

        # 仅保留最近 5 轮历史对话（最多 10 条消息）
        history = history[-10:]
        runtime_messages = [SystemMessage(content=system_content)]
        runtime_messages.extend(convert_history_to_messages(history, strip_thought=True))
        runtime_messages = normalize_messages_for_llm(runtime_messages)

        max_steps_str = await ConfigService.get("agent_max_iterations")
        max_steps = int(max_steps_str) if max_steps_str else 5

        if prefetch_had_citations:
            native_system_content = (
                f"{KnowledgeChatPrompts.PREFETCH_DONE_CORRECTION_MSG}\n\n{system_content}"
            )
        else:
            native_system_content = (
                f"{KnowledgeChatPrompts.SEARCH_CORRECTION_MSG}\n\n{system_content}"
            )

        # 预检索成功后仍保留 search_knowledge_base 注册，避免模型受用户附件/系统提示
        # 强制调用约束时触发 AgentScope ToolNotFoundError。
        react_tools = tools

        if not all(isinstance(tool, RuntimeToolSpec) for tool in react_tools):
            yield {
                "type": "error",
                "status": "error",
                "content": "Knowledge 工具链必须使用 AgentScope RuntimeToolSpec。",
            }
            return

        native_model_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        native_model = getattr(native_model_handle, "native_model", None)
        if native_model is None:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前模型适配器未提供 AgentScope native_model，无法执行知识库 AgentScope 原生工具链。",
            }
            return

        chunks_buffer = []
        full_text = ""
        async for chunk in self._execute_with_agentscope_native_agent(
            native_model=native_model,
            tools=react_tools,
            system_content=native_system_content,
            runtime_messages=runtime_messages,
            max_steps=max_steps,
        ):
            if "content" in chunk:
                content = str(chunk.get("content") or "")
                if self._valid_citation_ids:
                    content = filter_invalid_citation_markers(content, self._valid_citation_ids)
                full_text += content
                chunk = dict(chunk)
                chunk["content"] = content
            chunks_buffer.append(chunk)

        is_hallucinated = False
        if self._rag_empty:
            is_hallucinated = self._is_hallucinated_knowledge_reply(full_text)
        else:
            is_hallucinated = self._is_hallucinated_with_rag_reply(full_text)

        if is_hallucinated:
            logger.warning(
                f"[KnowledgeAgentRunner] Hallucination detected! RAG empty={self._rag_empty}, "
                f"but model generated: {full_text[:200]}..."
            )
            yield {
                "type": "log",
                "id": f"kb_guard_{uuid.uuid4().hex[:8]}",
                "title": "阻止无依据回答",
                "details": (
                    "知识库中未检索到相关文档，大模型尝试直接脑补回答，已拦截该输出。"
                    if self._rag_empty else
                    "检索到知识库文档，但模型生成了无任何来源引用的事实回答，已拦截该输出以防幻觉。"
                ),
                "status": "warning",
            }
            refusal_content = "⚠️ 抱歉，在系统知识库中未检索到相关内容，无法回答该问题。建议您更换关键词重新检索。"
            yield {"content": refusal_content}
        else:
            for chunk in chunks_buffer:
                yield chunk
