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
from app.services.ai.grounding.policy import GroundingRiskLevel
from app.services.ai.grounding.service import GroundingService
from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner
from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context
from app.services.metadata_rag_service import MetadataRagService
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage, AIMessage
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec, runtime_tool_spec_from_legacy_tool
from app.services.ai.knowledge_utils import (
    NO_KNOWLEDGE_DATASET_MESSAGE,
    KNOWLEDGE_BASE_DISABLED_USER_MESSAGE,
    collect_citation_ids_from_payload,
    filter_invalid_citation_markers,
    format_dataset_ids_for_tool,
    has_knowledge_citation_markers,
    is_knowledge_base_enabled,
    knowledge_prefetch_had_citations,
    resolve_knowledge_dataset_ids,
    text_has_valid_citation_markers,
)
from app.services.ai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 知识库智能体不挂载外网检索类隐式工具，避免 ReAct 误选百度/静态抓取替代 search_knowledge_base。
KNOWLEDGE_EXCLUDED_IMPLICIT_TOOLS = frozenset({
    "web_search_baidu",
    "fetch_static_web_url",
})


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
    def _looks_like_rag_followup(self, query: str, history: List[Dict[str, str]]) -> bool:
        """识别对上一轮检索结果的追问、翻译、总结、解释等上下文操作，以跳过本轮不必要的知识库重复检索。"""
        if not history or len(history) < 2:
            return False
        q = (query or "").strip().lower()
        if not q:
            return False
        
        # 1. 指代词特征
        vague_refs = ["刚才", "刚刚", "上面", "上轮", "上一轮", "这个", "这些", "它", "刚才说的", "前一个", "翻译结果", "上一个回答"]
        # 2. 格式化/翻译/总结/追问等上下文动作特征
        followup_actions = ["翻译", "英文", "english", "总结", "概括", "简短", "口语化", "解释一下第", "详细说明第", "写得通俗", "翻译成"]
        
        is_ref_match = any(ref in q for ref in vague_refs)
        is_action_match = any(act in q for act in followup_actions)
        return is_ref_match or is_action_match or (len(q) < 8 and is_action_match)

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
            seen = {spec.name for spec in tools}
            for tool in system_tools:
                name = str(getattr(tool, "name", "") or "")
                if not name or name in KNOWLEDGE_EXCLUDED_IMPLICIT_TOOLS or name in seen:
                    continue
                spec = runtime_tool_spec_from_legacy_tool(tool, source_type="system")
                tools.append(ToolRegistry._attach_evidence_metadata(spec.name, spec))
                seen.add(name)

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
            if MetadataRagService._is_service_unavailable(exc):
                output = MetadataRagService.knowledge_unavailable_hint(err_msg)
            else:
                output = f"[TOOL_ERROR] 自动检索知识库失败: {exc}"

        # 混合检索自适应召回退避逻辑 (特性 6)
        is_empty = False
        max_score = 0.0
        citations = []
        parsed = None
        try:
            import json
            parsed = json.loads(output)
            if isinstance(parsed, dict):
                is_empty = (parsed.get("status") == "empty")
                citations = parsed.get("citations", [])
                if citations:
                    scores = [float(c.get("similarity", 0.0)) for c in citations if "similarity" in c]
                    if scores:
                        max_score = max(scores)
        except Exception:
            pass

        similarity_threshold = 0.45
        try:
            from app.services.config_service import ConfigService
            sys_threshold = await ConfigService.get("knowledge_ragflow_similarity_threshold")
            if sys_threshold is not None:
                similarity_threshold = float(sys_threshold)
        except Exception:
            pass

        service_unavailable = self._is_knowledge_service_unavailable(output)
        parsed_ok = isinstance(parsed, dict)
        if parsed_ok and not service_unavailable and (is_empty or max_score < similarity_threshold):
            logger.info(f"[KnowledgeAgentRunner] Knowledge recall weak (max_score={max_score} < threshold={similarity_threshold}). Activating web search backoff...")
            web_tool_id = f"web_prefetch_{uuid.uuid4().hex[:8]}"
            web_started = time.time()
            yield {
                "type": "log",
                "id": web_tool_id,
                "title": "触发联网辅助检索",
                "details": f"知识库检索无高置信度结果（相似度: {max_score:.3f}，阈值: {similarity_threshold}），自适应触发百度联网检索辅助推理。",
                "status": "pending",
                "category": "tool",
                "started_at": int(web_started * 1000),
            }
            try:
                from app.services.ai.tools.advanced_auxiliary_tools import web_search_baidu_raw
                web_results = await web_search_baidu_raw(query=query, max_results=3)
                web_duration = (time.time() - web_started) * 1000
                yield {
                    "type": "log",
                    "id": web_tool_id,
                    "title": "联网辅助检索完成",
                    "details": f"成功融合 {len(web_results)} 个网页的最新参考事实作为推理支撑 (耗时 {web_duration:.0f}ms)",
                    "status": "success",
                    "category": "tool",
                    "execution_time_ms": web_duration,
                }
                
                kb_context = ""
                if isinstance(parsed, dict):
                    kb_context = parsed.get("content", "")
                else:
                    kb_context = output

                web_context_pieces = []
                new_citations = []
                
                # 给原有的知识库 citations 加上 source_type
                for c in citations:
                    c["source_type"] = "knowledge"
                    new_citations.append(c)
                    
                for idx, item in enumerate(web_results, 1):
                    title = item.get("title", "")
                    link = item.get("real_url") or item.get("link", "")
                    abstract = item.get("abstract", "")
                    content = item.get("extracted_content") or abstract
                    
                    web_context_pieces.append(f"文献 {idx} [标题: {title} | 来源: {link}]:\n{content}")
                    
                    web_chunk_id = f"web_{idx}_{uuid.uuid4().hex[:6]}"
                    new_citations.append({
                        "chunk_id": web_chunk_id,
                        "doc_name": f"网页: {title}",
                        "content": content,
                        "similarity": 1.0,
                        "source_type": "web",
                        "link": link
                    })
                    
                web_context = "\n\n【互联网参考事实文献】\n" + "\n\n".join(web_context_pieces) if web_context_pieces else ""
                combined_context = f"{kb_context}\n{web_context}"
                
                import json
                output = json.dumps({
                    "content": combined_context,
                    "citations": new_citations
                })
                self._valid_citation_ids = [c["chunk_id"] for c in new_citations]
                
            except Exception as web_exc:
                logger.error(f"[KnowledgeAgentRunner] Web search backoff failed: {web_exc}", exc_info=True)
                yield {
                    "type": "log",
                    "id": web_tool_id,
                    "title": "联网辅助检索失败",
                    "details": f"发生异常: {web_exc}",
                    "status": "error",
                    "category": "tool",
                }

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
            "__citations_raw__": observation.get("citation", {}).get("data") if observation.get("citation") else None,
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

        if not await is_knowledge_base_enabled():
            yield {
                "type": "error",
                "status": "error",
                "content": KNOWLEDGE_BASE_DISABLED_USER_MESSAGE,
            }
            return

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

        # 从 debug_options 读取会话级反幻觉开关（与模型覆盖等参数同通道）
        hallucination_check_enabled = bool(self.debug_options.get("hallucination_check", False))
        grounding_enabled = self._grounding_enabled()

        system_content = self.config.system_prompt or ""
        system_content = f"{KnowledgeChatPrompts.TURN_SYSTEM_HINT}\n\n{system_content}"

        # RAG 分类与复用决策
        from app.services.ai.intent_service import looks_like_accessible_resource_catalog_query

        is_catalog_query = looks_like_accessible_resource_catalog_query(user_question)
        is_followup = (not is_catalog_query) and self._looks_like_rag_followup(user_question, history)
        prefetched_knowledge_output: str | None = None
        prefetched_citations_raw: list | None = None
        knowledge_service_unavailable = False
        prefetch_had_citations = False
        knowledge_state_prompt = ""

        if is_catalog_query:
            yield {
                "type": "log",
                "id": f"kb_catalog_{uuid.uuid4().hex[:8]}",
                "title": "知识库目录清单",
                "details": (
                    "检测到权限/目录清单询问，跳过 search_knowledge_base 自动检索；"
                    "请使用 list_accessible_knowledge_bases 返回可访问知识库。"
                ),
                "status": "success",
                "category": "intent",
            }
            system_content += (
                "\n\n【本轮为知识库权限/目录清单】"
                "必须调用 list_accessible_knowledge_bases 列出当前用户可访问知识库；"
                "禁止调用 search_knowledge_base 检索文档正文，禁止编造权限清单。"
            )
        elif is_followup:
            yield {
                "type": "log",
                "id": f"kb_followup_{uuid.uuid4().hex[:8]}",
                "title": "自动复用知识库",
                "details": "检测到对上一轮回答的追问/操作（翻译/总结等），自动复用历史上下文，跳过知识库重新检索。",
                "status": "success",
                "category": "intent",
            }
            # 提取上一轮被成功引用的 ID，以便在追问中继续复用，通过防幻觉校验
            last_assistant_content = ""
            for msg in reversed(history):
                if msg.get("role") == "assistant":
                    last_assistant_content = str(msg.get("content") or "")
                    break
            if last_assistant_content:
                import re
                last_citations = re.findall(r"\[ID:\s*(\d+)\]", last_assistant_content)
                self._valid_citation_ids = set(last_citations)
            knowledge_state_prompt = KnowledgeChatPrompts.knowledge_state_block(
                search_status="REUSED",
                result_status="SUFFICIENT" if self._valid_citation_ids else "UNKNOWN",
                citation_count=len(self._valid_citation_ids),
                supplement_allowed=False,
            )
        else:
            resolved_dataset_ids, dataset_error = await resolve_knowledge_dataset_ids(
                query=user_question,
            )
            if dataset_error and not resolved_dataset_ids:
                async for chunk in self._yield_knowledge_no_dataset_abort():
                    yield chunk
                return

            prefetch_dataset_ids = format_dataset_ids_for_tool(resolved_dataset_ids)
            async for chunk in self._auto_invoke_search_knowledge_base(
                query=user_question,
                tools=tools,
                dataset_ids=prefetch_dataset_ids,
            ):
                if chunk.get("__knowledge_service_unavailable__"):
                    knowledge_service_unavailable = True
                if chunk.get("__knowledge_output__") is not None:
                    prefetched_knowledge_output = str(chunk["__knowledge_output__"])
                    prefetched_citations_raw = chunk.get("__citations_raw__")
                    prefetch_had_citations = knowledge_prefetch_had_citations(prefetched_knowledge_output)
                    continue
                yield chunk

            if knowledge_service_unavailable:
                async for chunk in self._yield_knowledge_fatal_abort(prefetched_knowledge_output):
                    yield chunk
                return

            knowledge_state_prompt = KnowledgeChatPrompts.knowledge_state_block(
                search_status="PREFETCHED",
                result_status="SUFFICIENT" if prefetch_had_citations else "INSUFFICIENT",
                citation_count=len(self._valid_citation_ids),
                supplement_allowed=not prefetch_had_citations,
            )

        self._rag_empty = False
        if knowledge_state_prompt:
            system_content += "\n\n" + knowledge_state_prompt
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
            user_name=self._runtime_user_name(),
            user_info=self.user_info,
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

        if is_followup:
            followup_hint = (
                "【平台已复用上一轮上下文】检测到本轮为针对上一次知识库回答的追问、翻译或格式调整动作，"
                "系统已自动复用上一轮检索到的事实文献上下文，跳过了本次 search_knowledge_base 重复检索。"
                "请直接在多轮对话历史中结合上一轮的回答来响应用户，无需再次检索。"
            )
            native_system_content = f"{followup_hint}\n\n{system_content}"
        elif prefetch_had_citations:
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

        # 幻觉检测自反思循环 (Reflection Loop) (特性 3)
        from app.services.ai.hallucination_evaluator import HallucinationEvaluator

        max_retries = 2
        retry_count = 0
        passed_guard = False
        last_guard_reason = "回答中存在尚未完全核实的知识库事实"
        
        current_system_content = native_system_content
        current_messages = list(runtime_messages)

        while retry_count <= max_retries:
            chunks_buffer = []
            full_text = ""
            
            async for chunk in self._execute_with_agentscope_native_agent(
                native_model=native_model,
                tools=react_tools,
                system_content=current_system_content,
                runtime_messages=current_messages,
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

            if not grounding_enabled:
                passed_guard = True
                break

            # 评估是否含有幻觉（受会话开关控制）
            evaluation = await HallucinationEvaluator.evaluate(
                query=user_question,
                context=prefetched_knowledge_output or "",
                response=full_text,
                enabled=hallucination_check_enabled,
            )

            is_hallucinated = evaluation.get("is_hallucinated", False)
            if not is_hallucinated:
                # 兜底原有的规则：如果 RAG 为空但模型编造了答案，或召回非空但缺少引用
                if self._rag_empty:
                    is_hallucinated = self._is_hallucinated_knowledge_reply(full_text)
                else:
                    is_hallucinated = self._is_hallucinated_with_rag_reply(full_text)

            if not is_hallucinated:
                passed_guard = True
                break

            retry_count += 1
            reason = evaluation.get("reason") or "回答中存在无依据的幻觉陈述或缺少必要引用"
            last_guard_reason = reason
            logger.warning(
                f"[KnowledgeAgentRunner] Hallucination detected! Attempt {retry_count} failed. "
                f"Reason: {reason}. Model output: {full_text[:150]}..."
            )

            if retry_count <= max_retries:
                # 发送拦截 log 事件告知前端
                yield {
                    "type": "log",
                    "id": f"kb_reflection_{uuid.uuid4().hex[:8]}",
                    "title": f"检测到无依据表述 (反思修正中 {retry_count}/{max_retries})",
                    "details": f"回答中发现与事实文献不符的描述（原因: {reason}），安全网关已拦截并命令其重写纠正。",
                    "status": "warning",
                }
                
                # 重新追加 messages 进行反思自纠偏
                current_messages.append(AIMessage(content=full_text))
                reflection_prompt = (
                    f"【网关安全警报】\n"
                    f"你刚才生成的回答被事实一致性网关拦截退回。原因：{reason}。\n"
                    f"请务必吸取教训，重新审查问题和提供的事实文献，绝对不允许包含文献中没有明确说明的外部假设、新参数或隐藏规则。\n"
                    f"请重新进行一次高度客观且无任何幻觉的严谨回答："
                )
                current_messages.append(HumanMessage(content=reflection_prompt))
            else:
                break

        if passed_guard:
            # 异步记录 Redis 引用行为数据埋点 (特性 4)
            if prefetched_citations_raw:
                try:
                    import re
                    from app.core.redis import get_redis
                    redis = await get_redis()
                    if redis:
                        citation_ids = set(re.findall(r"\[ID:\s*(\d+)\]", full_text))
                        current_date = time.strftime("%Y-%m-%d")
                        key_prefix = f"kb:citation:stats:{current_date}"

                        dataset_ids = list({
                            c.get("dataset_id")
                            for c in prefetched_citations_raw
                            if c.get("source_type", "knowledge") == "knowledge" and c.get("dataset_id")
                        })
                        if dataset_ids:
                            from app.services.knowledge_metrics_service import KnowledgeMetricsService
                            await KnowledgeMetricsService.resolve_dataset_names(dataset_ids)

                        for idx, c in enumerate(prefetched_citations_raw, 1):
                            ref_id = str(idx)
                            source_type = c.get("source_type", "knowledge")
                            is_cited = ref_id in citation_ids

                            # 1. 统计知识库维度
                            if source_type == "knowledge":
                                ds_id = c.get("dataset_id") or "default"
                                await redis.hincrby(f"{key_prefix}:dataset:search", ds_id, 1)
                                if is_cited:
                                    await redis.hincrby(f"{key_prefix}:dataset:citation", ds_id, 1)

                                # 2. 统计文档维度
                                doc_id = c.get("doc_id")
                                doc_name = c.get("doc_name") or "Unknown Document"
                                if doc_id:
                                    await redis.hset("kb:citation:doc_names", doc_id, doc_name)
                                    await redis.hincrby(f"{key_prefix}:document:search", doc_id, 1)
                                    if is_cited:
                                        await redis.hincrby(f"{key_prefix}:document:citation", doc_id, 1)
                except Exception as metric_err:
                    logger.warning(f"[KnowledgeAgentRunner] Redis metrics recording failed: {metric_err}")

            for chunk in chunks_buffer:
                yield chunk
        else:
            logger.warning(
                f"[KnowledgeAgentRunner] Hallucination check failed after {max_retries} retries. "
                f"Retaining the final answer with a grounding risk warning."
            )
            for chunk in chunks_buffer:
                yield chunk
            yield {
                "type": "log",
                "id": f"kb_guard_{uuid.uuid4().hex[:8]}",
                "title": "事实来源风险提示已追加",
                "details": last_guard_reason,
                "status": "warning",
                "category": "grounding",
            }
            yield GroundingService.warning_chunk(
                risk_level=GroundingRiskLevel.HIGH,
                reason=last_guard_reason,
            )
