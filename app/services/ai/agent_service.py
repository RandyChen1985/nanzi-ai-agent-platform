import json
import logging
import time
import uuid
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, AsyncGenerator

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.agent_manager import AgentManagerService
from app.services.ai.audit import AuditManager
from app.services.ai.config import AgentConfigProvider
from app.services.ai.context_manager import AgentContextManager
from app.services.ai.dispatcher import AgentDispatcher
from app.services.ai.memory_service import memory_service
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.prompt_assembler import (
    PromptAssemblyInput,
    assemble_system_prompt,
    resolve_prompt_assembler_flags,
)
from app.services.ai.runtime.session_run_lane import (
    ConversationRunBusyError,
    conversation_run_lane,
)
from app.services.ai.executors.common import _attachment_abs_path, extract_tokens_from_message
from app.services.ai.runtime.agentscope.compat import HumanMessage, SystemMessage
from app.core.orm import AsyncSessionLocal

logger = logging.getLogger(__name__)

AWAITING_RESUME_STATUSES = frozenset({"awaiting_permission", "awaiting_external_execution"})
NO_TOOL_EXECUTION_MESSAGE = "自动任务未实际调用任何工具"


def _accumulate_stream_content(full: str, chunk: Dict[str, Any]) -> str:
    """合并 SSE chunk 到会话正文；retraction 表示用新正文整体替换。"""
    if chunk.get("type") == "retraction":
        return str(chunk.get("content") or "")
    if "content" in chunk:
        return full + str(chunk["content"])
    return full


def _trace_has_tool_call(trace_buffer: Optional[List[AgentExecutionStep]]) -> bool:
    return any(getattr(step, "event_type", None) == "tool_call" for step in (trace_buffer or []))


class AgentService:
    USING_SUPERPOWERS_SKILL_ID = "using-superpowers"

    """
    Unified Orchestrator for AI Agent interactions.
    Now refactored to delegate execution to specialized Executors.
    """

    async def generate_greeting(self) -> str:
        """
        Return a fixed welcome message.
        """
        return AgentServicePrompts.GREETING

    async def _build_user_context_msg(self, user_info: Dict[str, Any]) -> Dict[str, str]:
        """
        Builds a read-only system message from verified API Key identity.
        """
        raw_name = user_info.get("user_name") or user_info.get("username", "Unknown User")
        user_id = str(user_info.get("user_id") or user_info.get("id") or "")
        real_name = user_info.get("real_name") or raw_name
        dept = user_info.get("dept_name") or user_info.get("department")
        org_path = user_info.get("org_path")
        dept_code = user_info.get("dept_code")
        role = user_info.get("role_name") or user_info.get("role")

        content = AgentServicePrompts.user_context_message(
            user_id=user_id or "unknown",
            raw_name=raw_name,
            real_name=real_name,
            dept=dept,
            dept_code=dept_code,
            org_path=org_path,
            role=role,
        )
        return {"role": "system", "content": content}

    @staticmethod
    def _parse_bool_config(value: Any, default: bool) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_int_config(value: Any, default: int, *, min_value: int, max_value: int | None = None) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        parsed = max(min_value, parsed)
        if max_value is not None:
            parsed = min(max_value, parsed)
        return parsed

    @staticmethod
    def _parse_float_config(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def _resolve_skill_full_load_policy(self) -> Dict[str, Any]:
        from app.services.config_service import ConfigService

        enabled_raw = await ConfigService.get("skill_auto_full_load_enabled", "true")
        min_score_raw = await ConfigService.get("skill_auto_full_load_min_score", "0.75")
        max_count_raw = await ConfigService.get("skill_auto_full_load_max_count", "1")
        max_bytes_raw = await ConfigService.get("skill_auto_full_load_max_bytes", "65536")
        return {
            "enabled": self._parse_bool_config(enabled_raw, True),
            "min_score": self._parse_float_config(min_score_raw, 0.75),
            "max_count": self._parse_int_config(max_count_raw, 1, min_value=0, max_value=3),
            "max_bytes": self._parse_int_config(max_bytes_raw, 65536, min_value=1024, max_value=262144),
        }

    @staticmethod
    def _should_preload_skill_full_instruction(
        *,
        match_source: str,
        match_score: Any = None,
        policy: Dict[str, Any],
        loaded_count: int,
    ) -> bool:
        if not policy.get("enabled"):
            return False
        if loaded_count >= int(policy.get("max_count") or 0):
            return False
        if match_source in {"mounted", "mention"}:
            return True
        if match_source == "scan":
            try:
                return float(match_score) >= float(policy.get("min_score") or 0.75)
            except (TypeError, ValueError):
                return False
        return False

    @staticmethod
    def _is_new_session_first_user_turn(messages: Optional[List[Dict[str, Any]]]) -> bool:
        """Whether the current context only contains the first user turn."""
        if not messages:
            return False
        conversation_roles = [
            str(m.get("role") or "").strip().lower()
            for m in messages
            if str(m.get("role") or "").strip().lower() in {"user", "assistant", "agent"}
        ]
        return conversation_roles == ["user"]

    @classmethod
    def _should_force_preload_scanned_skill(
        cls,
        *,
        skill_id: str,
        messages: Optional[List[Dict[str, Any]]],
    ) -> bool:
        return (
            skill_id == cls.USING_SUPERPOWERS_SKILL_ID
            and cls._is_new_session_first_user_turn(messages)
        )

    @classmethod
    def _ensure_first_turn_superpowers_candidate(
        cls,
        *,
        scanned_skills: List[Dict[str, Any]],
        available_skills: List[Dict[str, Any]],
        messages: Optional[List[Dict[str, Any]]],
        exclude_ids: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Ensure using-superpowers is considered on the first Main-agent turn."""
        if not cls._is_new_session_first_user_turn(messages):
            return scanned_skills
        excluded = exclude_ids or set()
        if cls.USING_SUPERPOWERS_SKILL_ID in excluded:
            return scanned_skills
        if any(skill.get("id") == cls.USING_SUPERPOWERS_SKILL_ID for skill in scanned_skills):
            return scanned_skills

        for skill in available_skills:
            if skill.get("id") != cls.USING_SUPERPOWERS_SKILL_ID:
                continue
            item = dict(skill)
            item["match_score"] = 1.0
            item["match_source"] = "scan"
            item["force_first_turn"] = True
            return [item] + scanned_skills
        return scanned_skills

    @staticmethod
    def _build_skill_injection(
        *,
        skill_name: str,
        skill_id: str,
        description: str,
        full_instruction: Optional[str] = None,
    ) -> str:
        if full_instruction:
            return AgentServicePrompts.skill_full_instruction_block(
                skill_name,
                skill_id,
                description,
                full_instruction,
            )
        return AgentServicePrompts.skill_summary_injection_block(
            skill_name,
            skill_id,
            description,
        )

    @staticmethod
    def _build_skill_log_chunk(skill_id: str, skill_name: str, details_msg: str) -> Dict[str, Any]:
        details = details_msg or (
            f"已识别候选流程「{skill_name}」(ID: {skill_id})。"
            "当前仅加载流程摘要；若本轮确需执行，系统会读取完整流程说明后再处理。"
        )
        is_full_enabled = "已预载完整" in details or "可直接按该流程执行" in details
        if is_full_enabled:
            return {
                "type": "log",
                "id": f"skill_enabled_{skill_id}",
                "title": f"已启用流程: {skill_name}",
                "details": details,
                "status": "success",
            }

        user_facing_details = details
        if "read_skill_instruction" in user_facing_details:
            user_facing_details = (
                f"已识别候选流程「{skill_name}」(ID: {skill_id})。"
                "当前仅加载流程摘要；若本轮确需执行，系统会读取完整流程说明后再处理。"
            )
        return {
            "type": "log",
            "id": f"skill_candidate_{skill_id}",
            "title": f"已识别候选流程: {skill_name}",
            "details": user_facing_details,
            "status": "success",
        }

    @staticmethod
    def _authorized_attachment_paths(messages: List[Dict[str, Any]]) -> List[str]:
        """Return server-resolved paths for attachments present in this chat context."""
        paths = {
            _attachment_abs_path(file_obj)
            for message in messages or []
            if message.get("role") == "user"
            for file_obj in message.get("files") or []
            if file_obj.get("url")
        }
        return sorted(path for path in paths if path)

    @staticmethod
    async def _quota_block_message(user_info: Optional[Dict[str, Any]]) -> Optional[str]:
        if not user_info:
            return None
        from app.services.quota_service import QuotaService

        async with AsyncSessionLocal() as quota_session:
            return await QuotaService(quota_session).check_before_call(user_info)

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        enable_multi_agent: bool = True,
        debug_options: Optional[Dict[str, Any]] = None,
        permission_options: Optional[Dict[str, Any]] = None,
        knowledge_dataset_ids: Optional[List[str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Main entry point for streaming chat.
        """
        trace_id = str(uuid.uuid4())
        trace_buffer: List[AgentExecutionStep] = []
        agent_config = None
        user_query = ""
        full_response_content = ""
        shared_state = {
            "agent_config": None,
            "execution_status": "success"
        }

        # 1. Initial Identity Chunk
        yield {"trace_id": trace_id, "status": "init"}

        lane_user_id = user_info.get("user_id") if user_info else None

        if user_info:
            quota_block = await self._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": trace_id,
                }
                return

        try:
            async with conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            ):
                from app.services.ai.executors.common import sanitize_client_messages_for_identity

                messages = sanitize_client_messages_for_identity(messages)

                # --- Memory Integration ---
                # If conversation_id is provided, we use server-side history
                if conversation_id:
                    u_id = lane_user_id
                    server_history = await memory_service.get_history(u_id, conversation_id)
                    user_msg = messages[-1] if messages else None

                    if user_msg and user_msg.get("role") == "user":
                        await memory_service.add_message(
                            u_id,
                            conversation_id,
                            "user",
                            user_msg["content"],
                            files=user_msg.get("files"),
                        )

                        from app.services.config_service import ConfigService
                        max_context = await ConfigService.get("agent_max_context_messages", "20")
                        try:
                            max_context = int(max_context)
                        except ValueError:
                            max_context = 20

                        context_history = server_history[-max_context:] if server_history else []
                        context_history = await self._maybe_compact_overflow(
                            server_history, context_history
                        )
                        messages = context_history + [user_msg]
                    else:
                        from app.services.config_service import ConfigService
                        max_context = await ConfigService.get("agent_max_context_messages", "20")
                        try:
                            max_context = int(max_context)
                        except ValueError:
                            max_context = 20
                        window = server_history[-max_context:] if server_history else []
                        messages = await self._maybe_compact_overflow(server_history, window)

                from app.utils.skill_metadata import enrich_messages_with_skill_meta

                enrich_messages_with_skill_meta(messages)

                user_query = messages[-1]["content"] if messages else ""

                # --- Handle explicit @mention in text ---
                import re
                if user_query and not (agent_id or agent_name):
                    mention_match = re.match(r'^[@＠]([^\s]+)\s+(.*)$', user_query, re.DOTALL)
                    if mention_match:
                        agent_name = mention_match.group(1)
                        user_query = mention_match.group(2).strip()
                        if messages:
                            messages[-1]["content"] = user_query
                        logger.info(f"Intercepted explicit @mention, routing directly to agent: {agent_name}")

                if not messages:
                    yield {"content": AgentServicePrompts.EMPTY_REQUEST}
                    return

                gen = self._run_chat_turn_stream(
                    messages=messages,
                    user_query=user_query,
                    agent_id=agent_id,
                    agent_name=agent_name,
                    version_id=version_id,
                    conversation_id=conversation_id,
                    user_info=user_info,
                    api_key=api_key,
                    enable_multi_agent=enable_multi_agent,
                    debug_options=debug_options,
                    permission_options=permission_options,
                    knowledge_dataset_ids=knowledge_dataset_ids,
                    trace_id=trace_id,
                    trace_buffer=trace_buffer,
                    start_time=asyncio.get_running_loop().time(),
                    shared_state=shared_state,
                )
                try:
                    async for chunk in gen:
                        if isinstance(chunk, dict) and "content" in chunk:
                            full_response_content += chunk["content"]
                        yield chunk
                finally:
                    await gen.aclose()
                agent_config = shared_state["agent_config"]
                execution_status = shared_state["execution_status"]
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

    @staticmethod
    async def _maybe_compact_overflow(
        full_history: List[Dict[str, Any]],
        window: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """超出上下文窗口时，把被丢弃的旧消息压缩成一条 system 摘录注入窗口最前。

        确定性、无额外 LLM 调用；由配置 ``agent_context_compaction_enabled`` 控制（默认开启）。
        """
        if not full_history or len(full_history) <= len(window):
            return window
        try:
            from app.services.config_service import ConfigService

            enabled_raw = await ConfigService.get("agent_context_compaction_enabled", "true")
            if str(enabled_raw or "").strip().lower() not in {"1", "true", "yes", "on"}:
                return window
            max_chars_raw = await ConfigService.get("agent_context_compaction_max_chars", "1200")
            try:
                max_chars = max(200, int(max_chars_raw))
            except (TypeError, ValueError):
                max_chars = 1200

            from app.services.ai.context_compaction import apply_context_compaction

            compacted = apply_context_compaction(
                full_history=full_history,
                window=window,
                max_chars=max_chars,
            )
            if len(compacted) != len(window):
                logger.info(
                    "[Compaction] Injected overflow digest: dropped=%d kept=%d",
                    len(full_history) - len(window),
                    len(window),
                )
            return compacted
        except Exception as exc:
            logger.warning("[Compaction] Failed to compact overflow history: %s", exc)
            return window

    @staticmethod
    async def _maybe_empty_response_fallback() -> Optional[str]:
        """模型本轮无可见文本时返回兜底话术；由配置开关控制（默认开启）。"""
        try:
            from app.services.config_service import ConfigService

            enabled_raw = await ConfigService.get("agent_empty_response_fallback_enabled", "true")
            if str(enabled_raw or "").strip().lower() not in {"1", "true", "yes", "on"}:
                return None
        except Exception:
            pass
        return AgentServicePrompts.EMPTY_RESPONSE_FALLBACK

    async def _resolve_and_verify_agent(
        self,
        *,
        messages: list[dict[str, str]],
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
        enable_multi_agent: bool,
        user_info: Optional[dict[str, Any]],
        trace_buffer: list[AgentExecutionStep],
        user_query: str,
    ) -> tuple[Any, Any, float, Optional[str]]:
        """解析并校验智能体配置与权限。
        返回: (agent_config, route_details, route_elapsed_ms, permission_denied_err_msg)
        """
        route_start = asyncio.get_running_loop().time()
        agent_config, route_details = await AgentContextManager.resolve_agent_config(
            messages,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            enable_multi_agent=enable_multi_agent,
            user_info=user_info,
        )
        route_elapsed_ms = (asyncio.get_running_loop().time() - route_start) * 1000

        if not agent_config:
            return None, None, route_elapsed_ms, None

        if route_details:
            logger.info(f"[Router] Routing decision found: {route_details}")
            from app.services.config_service import ConfigService
            router_model = await ConfigService.get("llm_model_name") or "DeepSeek-V3.2"
            r_thought = getattr(route_details, "reasoning", "No reasoning")
            r_conf = getattr(route_details, "confidence", 0.0)
            r_agent = getattr(route_details, "agent_id", "unknown")
            r_turn_labels = getattr(route_details, "turn_labels", []) or []
            r_relation = getattr(route_details, "relation_to_previous", "unknown")
            r_action_type = getattr(route_details, "user_action_type", "unknown")
            r_intent_info = getattr(route_details, "intent_info", None)

            trace_buffer.append(AgentExecutionStep(
                step_number=0,
                event_type="router",
                agent_name="Smart Router",
                model=router_model,
                tool_name="route_query",
                tool_input={"query": user_query},
                tool_output={
                    "thought": r_thought,
                    "selected_agent": r_agent,
                    "confidence": r_conf,
                    "turn_labels": r_turn_labels,
                    "relation_to_previous": r_relation,
                    "user_action_type": r_action_type,
                    "semantic_intent": getattr(r_intent_info, "intent", None),
                    "semantic_confidence": getattr(r_intent_info, "confidence", None),
                    "semantic_reasoning": getattr(r_intent_info, "reasoning", None),
                },
                status="success",
                execution_time_ms=route_elapsed_ms
            ))
        else:
            logger.info("[Router] No routing details (direct agent selection or fallback)")

        if user_info:
            u_role = user_info.get("role", "")
            u_id = user_info.get("user_id", user_info.get("id"))
            if u_role != "admin" and u_id:
                from app.services.permission_service import PermissionService
                async with AsyncSessionLocal() as session:
                    perm_service = PermissionService(session)
                    agent_id_str = str(agent_config.agent_id)
                    has_perm = await perm_service.check_permission(int(u_id), "agent", agent_id_str)
                    if not has_perm:
                        err_msg = AgentServicePrompts.permission_denied(agent_config.agent_name)
                        return agent_config, route_details, route_elapsed_ms, err_msg

        return agent_config, route_details, route_elapsed_ms, None

    async def _inject_skills(
        self,
        *,
        messages: list[dict[str, str]],
        user_query: str,
        agent_config: Any,
        skills_log_callback: Optional[callable] = None,
    ) -> list[str]:
        """挂载与自动匹配技能，返回 skills_injection。"""
        active_skills = []
        if messages and "files" in messages[-1] and messages[-1]["files"]:
            for file_obj in messages[-1]["files"]:
                if file_obj.get("type") == "skill":
                    active_skills.append(file_obj)

        mounted_skill_ids = {s.get("url") for s in active_skills if s.get("url")}
        skills_injection = []
        full_load_policy = await self._resolve_skill_full_load_policy()
        full_loaded_count = 0

        if active_skills:
            import os
            from app.core.config import settings
            from app.services.ai.skill_resolver import load_skill_md_content
            from app.utils.skill_metadata import parse_skill_frontmatter

            for skill_obj in active_skills:
                skill_id = skill_obj.get("url")
                if not skill_id:
                    continue
                skill_md_path = os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md")
                meta_override = skill_obj.get("skillMeta") or skill_obj.get("skill_meta")
                if meta_override and isinstance(meta_override, dict):
                    skill_name = str(meta_override.get("name") or skill_id)
                    description = str(meta_override.get("description") or "")
                elif os.path.exists(skill_md_path):
                    meta = parse_skill_frontmatter(skill_id, skill_md_path)
                    skill_name = meta.get("name") or skill_obj.get("filename", skill_id).replace(" (技能)", "")
                    description = meta.get("description") or ""
                else:
                    skill_name = skill_obj.get("filename", skill_id).replace(" (技能)", "")
                    description = ""
                    logger.warning("[Skills] Skill markdown not found at %s", skill_md_path)

                full_instruction = None
                if self._should_preload_skill_full_instruction(
                    match_source="mounted",
                    policy=full_load_policy,
                    loaded_count=full_loaded_count,
                ):
                    full_instruction = load_skill_md_content(
                        skill_id,
                        max_bytes=int(full_load_policy["max_bytes"]),
                    )
                    if full_instruction:
                        full_loaded_count += 1

                skills_injection.append(
                    self._build_skill_injection(
                        skill_name=skill_name,
                        skill_id=skill_id,
                        description=description,
                        full_instruction=full_instruction,
                    )
                )
                logger.info(
                    "[Skills] Matched mounted skill %s (%s).",
                    skill_id,
                    "full instruction preloaded" if full_instruction else "summary only",
                )

        if user_query:
            try:
                from app.services.ai.skill_resolver import (
                    load_skill_md_content,
                    resolve_skills_from_query,
                )

                for skill_meta in resolve_skills_from_query(user_query):
                    skill_id = skill_meta.get("id")
                    if not skill_id or skill_id in mounted_skill_ids:
                        continue
                    skill_name = skill_meta.get("name") or skill_id
                    description = skill_meta.get("description") or ""
                    full_instruction = None
                    if self._should_preload_skill_full_instruction(
                        match_source=str(skill_meta.get("match_source") or "mention"),
                        match_score=skill_meta.get("match_score"),
                        policy=full_load_policy,
                        loaded_count=full_loaded_count,
                    ):
                        full_instruction = load_skill_md_content(
                            skill_id,
                            max_bytes=int(full_load_policy["max_bytes"]),
                        )
                        if full_instruction:
                            full_loaded_count += 1
                    skills_injection.append(
                        self._build_skill_injection(
                            skill_name=skill_name,
                            skill_id=skill_id,
                            description=description,
                            full_instruction=full_instruction,
                        )
                    )
                    mounted_skill_ids.add(skill_id)
                    logger.info(
                        "[Skills] Auto-resolved skill %s from query (%s).",
                        skill_id,
                        "full instruction preloaded" if full_instruction else "summary only",
                    )
                    if skills_log_callback:
                        details_msg = ""
                        if full_instruction:
                            details_msg = (
                                f"已从本轮问题匹配「{skill_name}」(ID: {skill_id})。"
                                "已预载完整 SKILL.md 指令，本轮可直接按该流程执行。"
                            )
                        skills_log_callback(skill_id, skill_name, details_msg)
            except Exception as resolve_err:
                logger.warning("[Skills] Failed to auto-resolve skills from query: %s", resolve_err)

        if user_query and not skills_injection:
            try:
                from app.services.ai.skill_resolver import (
                    is_main_general_agent,
                    list_skill_metas,
                    load_skill_md_content,
                    scan_relevant_skills,
                    should_scan_skills_for_query,
                )
                from app.services.config_service import ConfigService

                if is_main_general_agent(agent_config):
                    scan_enabled_raw = await ConfigService.get("skill_auto_scan_enabled", "true")
                    scan_enabled = str(scan_enabled_raw or "true").strip().lower() in {
                        "1", "true", "yes", "on",
                    }
                    if scan_enabled:
                        min_score_raw = await ConfigService.get("skill_auto_scan_min_score", "0.45")
                        try:
                            min_score = float(min_score_raw) if min_score_raw is not None else 0.45
                        except (TypeError, ValueError):
                            min_score = 0.45
                        max_results_raw = await ConfigService.get("skill_auto_scan_max_results", "1")
                        try:
                            max_scan_results = int(max_results_raw) if max_results_raw is not None else 1
                        except (TypeError, ValueError):
                            max_scan_results = 1
                        max_scan_results = max(1, min(max_scan_results, 3))

                        scanned_skills = []
                        if should_scan_skills_for_query(user_query):
                            scanned_skills = scan_relevant_skills(
                                user_query,
                                exclude_ids=mounted_skill_ids,
                                max_results=max_scan_results,
                                min_score=min_score,
                            )
                        available_skills = list_skill_metas()
                        scanned_skills = self._ensure_first_turn_superpowers_candidate(
                            scanned_skills=scanned_skills,
                            available_skills=available_skills,
                            messages=messages,
                            exclude_ids=mounted_skill_ids,
                        )
                        scanned_skills = scanned_skills[:max_scan_results]

                        for skill_meta in scanned_skills:
                            skill_id = skill_meta.get("id")
                            if not skill_id or skill_id in mounted_skill_ids:
                                continue
                            skill_name = skill_meta.get("name") or skill_id
                            description = skill_meta.get("description") or ""
                            match_score = skill_meta.get("match_score")
                            full_instruction = None
                            force_full_instruction = self._should_force_preload_scanned_skill(
                                skill_id=skill_id,
                                messages=messages,
                            )
                            if force_full_instruction or self._should_preload_skill_full_instruction(
                                match_source=str(skill_meta.get("match_source") or "scan"),
                                match_score=match_score,
                                policy=full_load_policy,
                                loaded_count=full_loaded_count,
                            ):
                                full_instruction = load_skill_md_content(
                                    skill_id,
                                    max_bytes=int(full_load_policy["max_bytes"]),
                                )
                                if full_instruction:
                                    full_loaded_count += 1
                            skills_injection.append(
                                self._build_skill_injection(
                                    skill_name=skill_name,
                                    skill_id=skill_id,
                                    description=description,
                                    full_instruction=full_instruction,
                                )
                            )
                            mounted_skill_ids.add(skill_id)
                            logger.info(
                                "[Skills] Scanned skill %s from query (score=%s, %s).",
                                skill_id,
                                match_score,
                                "full instruction preloaded" if full_instruction else "summary only",
                            )
                            if skills_log_callback:
                                score_hint = f"（相关度 {match_score}）" if match_score is not None else ""
                                if full_instruction:
                                    force_hint = "新会话首轮门禁已强制启用；" if force_full_instruction else ""
                                    details_msg = (
                                        f"已根据本轮问题扫描技能库并匹配「{skill_name}」(ID: {skill_id}){score_hint}。"
                                        f"{force_hint}已预载完整 SKILL.md 指令，本轮可直接按该流程执行。"
                                    )
                                else:
                                    details_msg = (
                                        f"已根据本轮问题扫描技能库并匹配「{skill_name}」(ID: {skill_id}){score_hint}。"
                                        f"已注入摘要；模型须调用 read_skill_instruction 读取 SKILL.md 全文后再执行。"
                                    )
                                skills_log_callback(skill_id, skill_name, details_msg)
            except Exception as scan_err:
                logger.warning("[Skills] Failed to scan skills from query: %s", scan_err)

        if skills_injection:
            MAX_PRELOAD_SKILLS = 5
            if len(skills_injection) > MAX_PRELOAD_SKILLS:
                logger.info(f"[Skills] Too many skills ({len(skills_injection)}), truncating to top {MAX_PRELOAD_SKILLS}")
                skills_injection = skills_injection[:MAX_PRELOAD_SKILLS]
                skills_injection.append(
                    "=== [已截断] 系统中已挂载或解析出更多可用技能，出于上下文性能优化，其余技能摘要未全部载入。如有需要，模型应通过调用 list_available_skills 工具获取其余技能详细摘要 ==="
                )

        return skills_injection

    async def _load_memory_context(
        self,
        *,
        user_info: Optional[dict[str, Any]],
        early_turn_type: Any,
        debug_options: Optional[dict[str, Any]],
        user_query: str,
    ) -> tuple[Optional[str], Optional[dict], Optional[str], Optional[str]]:
        """加载记忆与 LTM 预加载。
        返回: (ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text)
        """
        ltm_profile: Optional[str] = None
        ltm_loaded_data: Optional[dict] = None
        ignore_ltm = False
        if debug_options and debug_options.get("ignore_ltm"):
            ignore_ltm = True

        from app.services.ai.turn_classifier import (
            should_inject_ltm,
            should_inject_memory_recall_hint,
            should_run_active_memory_preload,
        )

        if not ignore_ltm and should_inject_ltm(early_turn_type) and user_info:
            u_id = user_info.get("user_id", user_info.get("id"))
            if u_id:
                try:
                    from app.services.ai.memory_service import ltm_service
                    ltm_data = await asyncio.wait_for(ltm_service.fetch_memory(str(u_id)), timeout=0.2)
                    if ltm_data:
                        import json
                        ltm_formatted = json.dumps(ltm_data, ensure_ascii=False, indent=2)
                        ltm_profile = AgentServicePrompts.ltm_memory_profile(ltm_formatted)
                        ltm_loaded_data = ltm_data
                        logger.info(f"[LTM] Successfully loaded memory profile for user {u_id}")
                except Exception as ltm_err:
                    logger.warning(f"[LTM] Failed to inject long-term memory for user {u_id}: {ltm_err}")

        memory_recall_hint: Optional[str] = None
        if should_inject_memory_recall_hint(early_turn_type):
            try:
                from app.services.memory_config_service import MemoryConfigService
                from app.services.ai.memory_recall_policy import CROSS_SESSION_MEMORY_SYSTEM_HINT

                if await MemoryConfigService.get_bool("memory_service_enabled", True):
                    memory_recall_hint = CROSS_SESSION_MEMORY_SYSTEM_HINT
            except Exception as mem_hint_err:
                logger.warning("[Memory] Failed to inject cross-session recall hint: %s", mem_hint_err)

        preloaded_memories_text: Optional[str] = None
        if should_run_active_memory_preload(early_turn_type) and user_info and user_query:
            u_id = user_info.get("user_id", user_info.get("id"))
            if u_id:
                try:
                    from app.services.memory_config_service import MemoryConfigService
                    if await MemoryConfigService.get_bool("memory_service_enabled", True):
                        from app.services.ai.tools.memory_search_tool import parse_date_from_query
                        from app.services.ai.daily_summary_service import DailySummaryService
                        from app.services.ai.memory_index_service import MemoryIndexService

                        uid = str(u_id)
                        target_day = parse_date_from_query(user_query)
                        preloaded_memories = []

                        if target_day:
                            d_summary = await DailySummaryService.get_daily_summary(uid, target_day)
                            if d_summary:
                                preloaded_memories.append(
                                    AgentServicePrompts.daily_summary_section(target_day, d_summary)
                                )
                            d_sessions = await MemoryIndexService.list_session_summaries_for_day(uid, target_day)
                            if d_sessions:
                                sess_lines = []
                                for idx, s in enumerate(d_sessions, 1):
                                    sess_lines.append(
                                        AgentServicePrompts.session_summary_line(idx, s)
                                    )
                                preloaded_memories.append(
                                    AgentServicePrompts.day_session_records(target_day, sess_lines)
                                )
                        else:
                            is_recall_intent = any(w in user_query for w in AgentServicePrompts.RECALL_INTENT_KEYWORDS)
                            if is_recall_intent:
                                recent_sessions = await MemoryIndexService.list_summaries(uid, limit=3)
                                if recent_sessions:
                                    sess_lines = []
                                    for idx, s in enumerate(recent_sessions, 1):
                                        sess_lines.append(
                                            AgentServicePrompts.session_summary_line(idx, s)
                                        )
                                    preloaded_memories.append(
                                        AgentServicePrompts.recent_sessions_section(sess_lines)
                                    )

                        if preloaded_memories:
                            preloaded_memories_text = AgentServicePrompts.preloaded_memories(preloaded_memories)
                            logger.info(f"[ActiveMemory] Successfully preloaded memory context for user {u_id}")
                except Exception as recall_err:
                    logger.warning(f"[ActiveMemory] Failed to preload memory context: {recall_err}", exc_info=True)

        return ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text

    async def _dispatch_executor(
        self,
        *,
        agent_config: Any,
        user_query: str,
        messages: list[dict[str, str]],
        trace_id: str,
        trace_buffer: list[AgentExecutionStep],
        debug_options: Optional[dict[str, Any]],
        permission_options: Optional[dict[str, Any]],
        user_info: Optional[dict[str, Any]],
        conversation_id: Optional[str],
        session_turn: Optional[tuple],
        route_hints: Optional[dict],
    ) -> Any:
        """调度并返回执行器实例。"""
        executor = await AgentDispatcher.dispatch(
            agent_config,
            user_query,
            messages,
            trace_id,
            trace_buffer,
            debug_options,
            permission_options,
            user_info,
            conversation_id,
            shared_turn=session_turn,
            route_hints=route_hints,
        )
        return executor


    async def _run_chat_turn_stream(
        self,
        *,
        messages: List[Dict[str, str]],
        user_query: str,
        agent_id: Optional[str],
        agent_name: Optional[str],
        version_id: Optional[str],
        conversation_id: Optional[str],
        user_info: Optional[Dict[str, Any]],
        api_key: Optional[str],
        enable_multi_agent: bool,
        debug_options: Optional[Dict[str, Any]],
        permission_options: Optional[Dict[str, Any]],
        knowledge_dataset_ids: Optional[List[str]],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        start_time: float,
        shared_state: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Internal turn runner; must be called inside conversation run lane when enabled."""
        agent_config = None
        full_response_content = ""
        execution_status = "success"
        has_data_output = False
        executor = None

        try:
            # 1. Resolve and Verify Agent Configuration and Permissions
            agent_config, route_details, route_elapsed_ms, err_msg = await self._resolve_and_verify_agent(
                messages=messages,
                agent_id=agent_id,
                agent_name=agent_name,
                version_id=version_id,
                enable_multi_agent=enable_multi_agent,
                user_info=user_info,
                trace_buffer=trace_buffer,
                user_query=user_query,
            )

            if agent_config and shared_state is not None:
                shared_state["agent_config"] = agent_config

            if not agent_config:
                yield {"content": AgentServicePrompts.NO_AGENT_CONFIG}
                return

            route_hints = None
            route_intent_evidence = None
            if agent_id or agent_name:
                route_hints = {"direct_agent_selection": True}

            if route_details:
                r_thought = getattr(route_details, "reasoning", "No reasoning")
                r_conf = getattr(route_details, "confidence", 0.0)
                r_agent = getattr(route_details, "agent_id", "unknown")
                r_turn_labels = getattr(route_details, "turn_labels", []) or []
                r_relation = getattr(route_details, "relation_to_previous", "unknown")
                r_action_type = getattr(route_details, "user_action_type", "unknown")
                route_intent_evidence = getattr(route_details, "intent_info", None)
                route_hints = {
                    "turn_labels": r_turn_labels,
                    "relation_to_previous": r_relation,
                    "user_action_type": r_action_type,
                    "semantic_intent": getattr(route_intent_evidence, "intent", None),
                    "semantic_confidence": getattr(route_intent_evidence, "confidence", None),
                    "semantic_reasoning": getattr(route_intent_evidence, "reasoning", None),
                }
                yield {
                    "type": "router_log",
                    "thought": r_thought,
                    "confidence": r_conf,
                    "selected_agent": r_agent,
                    "turn_labels": r_turn_labels,
                    "relation_to_previous": r_relation,
                    "user_action_type": r_action_type,
                    "semantic_intent": getattr(route_intent_evidence, "intent", None),
                    "semantic_confidence": getattr(route_intent_evidence, "confidence", None),
                    "semantic_reasoning": getattr(route_intent_evidence, "reasoning", None),
                    "status": "success",
                    "execution_time_ms": route_elapsed_ms
                }

            if err_msg:
                yield {"content": err_msg}
                execution_status = "denied"
                return

            from app.services.ai.knowledge_utils import (
                build_rag_retrieval_debug_meta,
                merge_request_knowledge_dataset_ids,
            )

            request_knowledge_dataset_ids = merge_request_knowledge_dataset_ids(
                knowledge_dataset_ids,
                messages,
            )

            await AgentContextManager.setup_context(
                config=agent_config,
                debug_options=debug_options,
                user_info=user_info,
                api_key=api_key,
                conversation_id=conversation_id,
                knowledge_dataset_ids=request_knowledge_dataset_ids,
                authorized_attachment_paths=self._authorized_attachment_paths(messages),
                trace_buffer=trace_buffer,
            )

            # 2. Inject Active Skills
            matched_skills_to_log = []
            def skills_log_callback(skill_id, skill_name, details_msg):
                matched_skills_to_log.append((skill_id, skill_name, details_msg))

            skills_injection = await self._inject_skills(
                messages=messages,
                user_query=user_query,
                agent_config=agent_config,
                skills_log_callback=skills_log_callback,
            )

            for skill_id, skill_name, details_msg in matched_skills_to_log:
                yield self._build_skill_log_chunk(skill_id, skill_name, details_msg)

            from app.services.ai.turn_classifier import (
                TurnType,
                TurnClassification,
                resolve_turn_for_session,
                should_inject_ltm,
                should_inject_memory_recall_hint,
                should_inject_user_context,
                should_run_active_memory_preload,
                turn_type_label,
                default_thought_expanded,
            )
            from app.services.ai.intent_service import IntentType

            can_do_data = "data_query" in (agent_config.capabilities or [])
            agent_engine_config = agent_config.engine_config or {}
            agent_bound_dataset_ids = agent_engine_config.get("dataset_ids") or []
            
            agent_has_knowledge_tool = False
            if getattr(agent_config, "tools", None):
                for t in agent_config.tools:
                    name = ""
                    if isinstance(t, str):
                        name = t
                    elif hasattr(t, "name"):
                        name = getattr(t, "name")
                    elif isinstance(t, dict) and "name" in t:
                        name = t["name"]
                    if name == "search_knowledge_base":
                        agent_has_knowledge_tool = True
                        break
            
            agent_has_knowledge_binding = (
                "knowledge_base" in (agent_config.capabilities or [])
                and bool(agent_bound_dataset_ids)
                and agent_has_knowledge_tool
            )
            turn_kwargs = {
                "user_query": user_query,
                "messages": messages,
                "user_info": user_info,
                "conversation_id": conversation_id,
                "knowledge_dataset_ids": request_knowledge_dataset_ids or None,
                "agent_has_knowledge_binding": agent_has_knowledge_binding,
                "intent_evidence": route_intent_evidence,
            }
            explicit_knowledge_context = bool(request_knowledge_dataset_ids or agent_has_knowledge_binding)
            if can_do_data and not explicit_knowledge_context:
                turn_classification = TurnClassification(
                    turn_type=TurnType.DATA_QUERY_REQUEST,
                    reasoning="ChatBI 轮次由 DataQueryExecutor 内部请求类别分析器最终判定",
                    skip_intent_llm=True,
                    intent=IntentType.DATA_QUERY,
                )
                turn_intent_info = None
                turn_intent_elapsed_ms = 0.0
                session_turn = None
            elif can_do_data:
                turn_classification, turn_intent_info, turn_intent_elapsed_ms = await resolve_turn_for_session(
                    **turn_kwargs,
                    can_do_data=True,
                )
                session_turn = (turn_classification, turn_intent_info, turn_intent_elapsed_ms)
            else:
                turn_classification, turn_intent_info, turn_intent_elapsed_ms = await resolve_turn_for_session(
                    **turn_kwargs,
                    can_do_data=False,
                )
                session_turn = (turn_classification, turn_intent_info, turn_intent_elapsed_ms)

            early_turn_type = turn_classification.turn_type
            if can_do_data and turn_classification.turn_type == TurnType.DATA_QUERY_REQUEST:
                turn_display_label = "ChatBI 请求类别分析"
            else:
                turn_display_label = turn_type_label(turn_classification.turn_type)

            if turn_classification.turn_type == TurnType.KNOWLEDGE:
                agent_config = await AgentContextManager.enrich_for_knowledge_turn(
                    agent_config,
                    user_query=user_query,
                )
                await AgentContextManager.setup_context(
                    config=agent_config,
                    debug_options=debug_options,
                    user_info=user_info,
                    api_key=api_key,
                    conversation_id=conversation_id,
                    knowledge_dataset_ids=request_knowledge_dataset_ids,
                    authorized_attachment_paths=self._authorized_attachment_paths(messages),
                    require_explicit_dataset=True,
                    trace_buffer=trace_buffer,
                )

            # 3. Load Memory Context
            ltm_profile, ltm_loaded_data, memory_recall_hint, preloaded_memories_text = await self._load_memory_context(
                user_info=user_info,
                early_turn_type=early_turn_type,
                debug_options=debug_options,
                user_query=user_query,
            )

            user_profile = None
            if user_info and should_inject_user_context(early_turn_type):
                id_msg = await self._build_user_context_msg(user_info)
                user_profile = id_msg.get("content")

            # --- 主助手：动态专家清单 + sub_agent_call 通讯录 ---
            agent_system_prompt = agent_config.system_prompt
            sub_agents_context = None
            from app.services.ai.skill_resolver import is_main_general_agent
            if is_main_general_agent(agent_config):
                try:
                    from app.core.orm import AsyncSessionLocal
                    from app.models.agent import AIAgent
                    from app.services.ai.agent_roster import (
                        AGENT_ROSTER_PLACEHOLDER,
                        build_sub_agents_context,
                        format_agent_roster_markdown,
                        inject_agent_roster,
                        resolve_delegable_system_agents_for_user,
                    )

                    async with AsyncSessionLocal() as session:
                        delegable_agents = await resolve_delegable_system_agents_for_user(
                            session,
                            user_info=user_info,
                            current_agent_id=agent_config.agent_id,
                        )
                        current_agent_row = await session.get(AIAgent, agent_config.agent_id)
                        current_desc = (current_agent_row.description if current_agent_row else "") or ""
                        if AGENT_ROSTER_PLACEHOLDER in (agent_system_prompt or ""):
                            roster_md = format_agent_roster_markdown(
                                delegable_agents,
                                current_display_name=agent_config.agent_display_name or agent_config.agent_name or "主助手",
                                current_description=current_desc,
                            )
                            agent_system_prompt = inject_agent_roster(agent_system_prompt, roster_md)
                        sub_agents_context = build_sub_agents_context(delegable_agents)
                except Exception as sa_err:
                    logger.warning(f"Failed to build main-agent roster/sub-agents context: {sa_err}")

            from app.core.config import settings
            cache_boundary_enabled, cache_reorder_enabled = await resolve_prompt_assembler_flags()
            assembled_prompt = assemble_system_prompt(
                PromptAssemblyInput(
                    agent_system_prompt=agent_system_prompt,
                    agent_config=agent_config,
                    engine_type=agent_config.engine_type or "LOCAL",
                    skills_injection=skills_injection,
                    skills_already_loaded=bool(skills_injection),
                    skills_dir=settings.SKILLS_DIR,
                    ltm_profile=ltm_profile,
                    memory_recall_hint=memory_recall_hint,
                    preloaded_memories=preloaded_memories_text,
                    user_profile=user_profile,
                    cache_boundary_enabled=cache_boundary_enabled,
                    cache_reorder_enabled=cache_reorder_enabled,
                    sub_agents_context=sub_agents_context,
                )
            )
            agent_config.system_prompt = assembled_prompt.full_text
            if debug_options and debug_options.get("return_raw_prompt"):
                debug_options.setdefault("prompt_assembler_meta", {})
                debug_options["prompt_assembler_meta"] = {
                    "stable_chars": len(assembled_prompt.stable_prefix),
                    "dynamic_chars": len(assembled_prompt.dynamic_suffix),
                    "cache_boundary_enabled": assembled_prompt.cache_boundary_enabled,
                    "cache_reorder_enabled": assembled_prompt.cache_reorder_enabled,
                }

            # --- Debug Overrides ---
            if debug_options:
                if debug_options.get("system_prompt_override"):
                    logger.info(f"[Debug] Overriding System Prompt for Trace {trace_id}")
                    agent_config.system_prompt = debug_options["system_prompt_override"]
                    yield {
                        "type": "log",
                        "title": "Debug: Prompt Override",
                        "details": "System Prompt 已被调试配置临时覆盖",
                        "status": "success",
                        "isDebug": True
                    }

                if debug_options.get("injected_context"):
                    context_data = debug_options["injected_context"]
                    logger.info(f"[Debug] Injecting Context: {context_data}")
                    ctx_lines = []
                    for k, v in context_data.items():
                        if k not in ["device_type", "display_hint"]:
                            ctx_lines.append(f"- **{k}**: {v}")
                    device_type = context_data.get("device_type", "Unknown")
                    ui_instr = ""
                    if "移动端" in device_type or "小屏幕" in device_type:
                        ui_instr = AgentServicePrompts.MOBILE_UI_RULES
                    elif "桌面端" in device_type or "大屏幕" in device_type:
                        ui_instr = AgentServicePrompts.DESKTOP_UI_RULES

                    context_str = "\n".join(ctx_lines)
                    injection_msg = {
                        "role": "system",
                        "content": AgentServicePrompts.session_runtime_context(context_str, device_type, ui_instr)
                    }
                    messages.insert(1, injection_msg)

            if debug_options and debug_options.get("return_raw_prompt"):
                raw_messages = []
                raw_messages.extend(messages)
                yield {
                    "type": "debug",
                    "subtype": "raw_prompt",
                    "data": raw_messages
                }

            from app.services.config_service import ConfigService
            default_model = await ConfigService.get("llm_model_name") or "DeepSeek-V3.2"
            actual_model = agent_config.model_name or default_model
            if debug_options and debug_options.get("model"):
                actual_model = debug_options["model"]
                logger.info(f"[Debug] Overriding Model to: {actual_model}")

            agent_config.model_name = actual_model

            meta_event: Dict[str, Any] = {
                "type": "meta",
                "agent_name": agent_config.agent_name,
                "agent_display_name": agent_config.agent_display_name or agent_config.agent_name,
                "model": actual_model,
                "turn_type": turn_classification.turn_type.value,
                "turn_type_label": turn_display_label,
                "thought_expanded_default": default_thought_expanded(turn_classification.turn_type),
            }
            if ltm_profile and ltm_loaded_data:
                meta_event["ltm_applied"] = True
                meta_event["ltm_data"] = ltm_loaded_data
            if (
                turn_classification.turn_type == TurnType.KNOWLEDGE
                or request_knowledge_dataset_ids
                or (agent_config.engine_config or {}).get("dataset_ids")
            ):
                try:
                    meta_event["rag_retrieval"] = await build_rag_retrieval_debug_meta()
                except Exception as rag_meta_err:
                    logger.warning("[AgentService] Failed to build rag_retrieval meta: %s", rag_meta_err)
            yield meta_event

            # 4. Dispatch Executor
            secondary_agents = getattr(route_details, "secondary_agents", []) if route_details else []

            if enable_multi_agent and secondary_agents:
                async for chunk in self._execute_multi_agent(
                    agent_config,
                    secondary_agents,
                    user_query,
                    messages,
                    trace_id,
                    trace_buffer,
                    debug_options,
                    permission_options,
                    user_info,
                    api_key,
                    conversation_id,
                    session_turn,
                    route_hints,
                ):
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    if chunk.get("type") == "permission_required":
                        execution_status = "awaiting_permission"
                    elif chunk.get("type") == "external_execution_required":
                        execution_status = "awaiting_external_execution"
                    elif chunk.get("type") == "error" or chunk.get("status") == "error":
                        execution_status = "error"
                    yield chunk
            else:
                executor = await self._dispatch_executor(
                    agent_config=agent_config,
                    user_query=user_query,
                    messages=messages,
                    trace_id=trace_id,
                    trace_buffer=trace_buffer,
                    debug_options=debug_options,
                    permission_options=permission_options,
                    user_info=user_info,
                    conversation_id=conversation_id,
                    session_turn=session_turn,
                    route_hints=route_hints,
                )

                yield {
                    "type": "log",
                    "title": "分析用户请求并进行意图识别",
                    "details": f"{turn_display_label}。{turn_classification.reasoning}",
                    "status": "success",
                    "category": "intent",
                    "turn_type": turn_classification.turn_type.value,
                    "execution_time_ms": turn_intent_elapsed_ms,
                }

                async for chunk in executor.execute(messages):
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    if chunk.get("type") == "permission_required":
                        execution_status = "awaiting_permission"
                    elif chunk.get("type") == "external_execution_required":
                        execution_status = "awaiting_external_execution"
                    elif chunk.get("type") == "error" or chunk.get("status") == "error":
                        execution_status = "error"
                    yield chunk

                resolve_has_data_output = getattr(executor, "resolve_has_data_output", None)
                if callable(resolve_has_data_output):
                    has_data_output = bool(resolve_has_data_output())

            # --- Empty Response Fallback ---
            if (
                execution_status == "success"
                and not (full_response_content or "").strip()
            ):
                fallback_text = await self._maybe_empty_response_fallback()
                if fallback_text:
                    full_response_content = fallback_text
                    yield {"content": fallback_text, "status": "success"}

            requires_tool_execution = bool(
                user_info
                and user_info.get("is_scheduled_task")
                and user_info.get("requires_tool_execution")
            )
            if (
                requires_tool_execution
                and execution_status == "success"
                and not _trace_has_tool_call(trace_buffer)
            ):
                execution_status = "no_tool_execution"
                no_tool_message = (
                    f"{NO_TOOL_EXECUTION_MESSAGE}，本次只产生了模型回复，没有产生工具调用；"
                    "已按未完成处理，请检查任务指令或智能体工具配置。"
                )
                full_response_content = (
                    f"{full_response_content}\n\n{no_tool_message}"
                    if full_response_content
                    else no_tool_message
                )
                yield {
                    "type": "error",
                    "status": "error",
                    "content": no_tool_message,
                }

            p_tokens, c_tokens, t_tokens = 0, 0, 0
            try:
                from app.services.ai.audit import aggregate_tokens_from_trace_buffer
                p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
            except Exception as agg_err:
                logger.warning(f"Failed to aggregate tokens for session: {agg_err}")

            if p_tokens or c_tokens:
                yield {
                    "type": "meta",
                    "prompt_tokens": p_tokens,
                    "completion_tokens": c_tokens,
                    "total_tokens": t_tokens
                }

            if has_data_output and execution_status == "success":
                yield {"type": "meta", "has_data_output": True}

            if conversation_id and full_response_content:
                u_id = user_info.get("user_id") if user_info else None
                handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
                asyncio.create_task(memory_service.add_message(
                    u_id,
                    conversation_id,
                    "assistant",
                    full_response_content,
                    trace_id=trace_id,
                    agent_name=handled_by,
                    prompt_tokens=p_tokens,
                    completion_tokens=c_tokens,
                    has_data_output=has_data_output or None,
                ))

        except Exception as e:
            logger.error(f"Execution Error: {str(e)}", exc_info=True)
            execution_status = "error"
            from app.services.ai.multimodal_support import format_execution_error

            model_name = getattr(agent_config, "model_name", None) if agent_config else None
            yield {
                "type": "error",
                "content": format_execution_error(str(e), model_name=model_name),
                "status": "error",
            }
        finally:
            end_time = asyncio.get_running_loop().time()
            duration = (end_time - start_time) * 1000

            is_scheduled_task = bool(user_info and user_info.get("is_scheduled_task"))
            if execution_status not in AWAITING_RESUME_STATUSES or is_scheduled_task:
                await AuditManager.log_transaction(
                     trace_id, agent_config, user_query, full_response_content,
                     user_info, execution_status, duration, trace_buffer,
                     conversation_id=conversation_id
                )

            if (
                conversation_id
                and execution_status == "success"
                and full_response_content
                and user_info
                and user_info.get("user_id")
            ):
                from app.services.ai.session_summary_service import SessionSummaryService
                u_id = user_info.get("user_id")
                asyncio.create_task(
                    SessionSummaryService.merge_session_summary(
                        str(u_id), conversation_id, full_response_content
                    )
                )



    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        version_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        user_info: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        enable_multi_agent: bool = True,
        permission_options: Optional[Dict[str, Any]] = None,
        knowledge_dataset_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Non-streaming wrapper for chat completion.
        Consumes the stream and returns the final result.
        """
        full_content = ""
        trace_id = ""
        agent_name_resp = ""
        final_status = "success"

        async for chunk in self.chat_completion_stream(
            messages,
            agent_id=agent_id,
            agent_name=agent_name,
            version_id=version_id,
            conversation_id=conversation_id,
            user_info=user_info,
            api_key=api_key,
            enable_multi_agent=enable_multi_agent,
            permission_options=permission_options,
            knowledge_dataset_ids=knowledge_dataset_ids,
        ):
            if "trace_id" in chunk and chunk.get("status") == "init":
                trace_id = chunk["trace_id"]
            chunk_type = chunk.get("type")
            if chunk_type == "permission_required":
                final_status = "awaiting_permission"
            elif chunk_type == "external_execution_required":
                final_status = "awaiting_external_execution"
            elif chunk_type == "error" or chunk.get("status") == "error":
                final_status = "error"
            if "content" in chunk:
                full_content += chunk["content"]
            if "agent_name" in chunk:
                agent_name_resp = chunk["agent_name"]

        return {
            "content": full_content,
            "intent": "general_chat", # Simplified, real intent is in stream but not easily exposed here without refactor
            "trace_id": trace_id,
            "agent_name": agent_name_resp,
            "status": final_status,
        }

    async def resume_agentscope_permission_stream(
        self,
        *,
        permission_request_id: str,
        confirmed: bool,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.confirmations import (
            pending_agentscope_confirmations,
        )

        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.pop_async(
            permission_request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": "工具确认请求不存在或已过期，请重新发起本轮对话。",
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": "当前用户无权确认该工具调用。",
            }
            return

        if pending.snapshot.kind == "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求为外部执行挂起，请使用 external execution 恢复接口。",
            }
            return

        if confirmed and user_info:
            quota_block = await self._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner = self._build_agentscope_runner_from_pending(pending, user_info=user_info)
        await self._restore_runner_execution_context(
            runner,
            pending,
            user_info=user_info,
        )

        yield {
            "type": "permission_result",
            "status": "success" if confirmed else "rejected",
            "permission_request_id": permission_request_id,
            "tool_call_id": getattr(pending.tool_call, "id", None),
        }

        full_response_content = ""
        execution_status = "success" if confirmed else "rejected"
        start_time = asyncio.get_running_loop().time()
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            async with conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ):
                async for chunk in runner.resume_agentscope_native_confirmation(
                    pending,
                    confirmed=confirmed,
                ):
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    yield chunk
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

        p_tokens, c_tokens, t_tokens = 0, 0, 0
        trace_buffer = runner.trace_buffer
        try:
            from app.services.ai.audit import aggregate_tokens_from_trace_buffer
            p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
        except Exception as agg_err:
            logger.warning(f"Failed to aggregate tokens after permission resume: {agg_err}")

        if p_tokens or c_tokens:
            yield {
                "type": "meta",
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            }

        agent_config = runner.config
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        user_query = (pending.state or {}).get("user_query") or ""

        if conversation_id and full_response_content:
            u_id = user_info.get("user_id") if user_info else pending.user_id
            handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
            asyncio.create_task(memory_service.add_message(
                u_id,
                conversation_id,
                "assistant",
                full_response_content,
                trace_id=pending.trace_id,
                agent_name=handled_by,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
            ))

        duration = (asyncio.get_running_loop().time() - start_time) * 1000
        asyncio.create_task(AuditManager.log_transaction(
            pending.trace_id,
            agent_config,
            user_query,
            full_response_content,
            user_info,
            execution_status,
            duration,
            trace_buffer,
            conversation_id=conversation_id,
        ))

        if (
            conversation_id
            and execution_status == "success"
            and full_response_content
            and user_info
            and user_info.get("user_id")
        ):
            from app.services.ai.session_summary_service import SessionSummaryService
            asyncio.create_task(
                SessionSummaryService.merge_session_summary(
                    str(user_info.get("user_id")), conversation_id, full_response_content
                )
            )

    async def _restore_runner_execution_context(
        self,
        runner: Any,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """工具确认/外部执行恢复前重建 AgentContext，避免 user_id 等会话信息丢失。"""
        effective_user_info = user_info or getattr(runner, "user_info", None)
        if effective_user_info and getattr(runner, "config", None) is not None:
            from app.services.ai.context_manager import AgentContextManager

            await AgentContextManager.setup_context(
                config=runner.config,
                user_info=effective_user_info,
                api_key=effective_user_info.get("api_key"),
                conversation_id=(
                    getattr(runner, "conversation_id", None)
                    or pending.snapshot.conversation_id
                ),
                trace_buffer=getattr(runner, "trace_buffer", None) or [],
            )
            return
        if hasattr(runner, "_ensure_agent_context"):
            runner._ensure_agent_context()

    def _build_agentscope_runner_from_pending(
        self,
        pending: Any,
        *,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Any:
        runner = pending.runner
        if runner is not None:
            if user_info:
                runner.user_info = {**(runner.user_info or {}), **user_info}
            return runner

        ctx = pending.snapshot.runner_context or {}
        if ctx.get("runner_type") == "data":
            from app.services.ai.runners.data_agent_runner import DataAgentRunner

            return DataAgentRunner.from_runner_context(
                runner_context=ctx,
                trace_id=pending.trace_id,
                trace_buffer=[],
                user_info=user_info,
                conversation_id=pending.snapshot.conversation_id,
            )

        from app.services.ai.runners.assistant_agent_runner import AssistantAgentRunner

        if ctx.get("runner_type") in ("assistant", "general"):
            return AssistantAgentRunner.from_runner_context(
                runner_context=ctx,
                trace_id=pending.trace_id,
                trace_buffer=[],
                user_info=user_info,
                conversation_id=pending.snapshot.conversation_id,
            )

        raise ValueError(f"Unsupported runner_type for resume: {ctx.get('runner_type')!r}")

    @staticmethod
    def _build_external_execution_results(results: List[Dict[str, Any]]) -> List[Any]:
        from agentscope.message import ToolResultBlock, ToolResultState

        state_map = {
            "success": ToolResultState.SUCCESS,
            "error": ToolResultState.ERROR,
            "running": ToolResultState.RUNNING,
            "interrupted": ToolResultState.INTERRUPTED,
            "denied": ToolResultState.DENIED,
        }
        return [
            ToolResultBlock(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                output=str(item.get("output") or ""),
                state=state_map.get(str(item.get("state") or "success").lower(), ToolResultState.SUCCESS),
            )
            for item in results
        ]

    async def resume_agentscope_external_execution_stream(
        self,
        *,
        external_execution_request_id: str,
        results: List[Dict[str, Any]],
        user_info: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.confirmations import (
            pending_agentscope_confirmations,
        )

        current_user_id = None
        if user_info:
            current_user_id = user_info.get("user_id") or user_info.get("id")

        pending = await pending_agentscope_confirmations.pop_async(
            external_execution_request_id,
            user_id=current_user_id,
        )
        if not pending:
            yield {
                "type": "error",
                "status": "error",
                "content": "外部执行请求不存在或已过期，请重新发起本轮对话。",
            }
            return

        if pending.user_id and current_user_id and str(current_user_id) != str(pending.user_id):
            yield {
                "type": "error",
                "status": "error",
                "content": "当前用户无权提交该外部执行结果。",
            }
            return

        if pending.snapshot.kind != "external":
            yield {
                "type": "error",
                "status": "error",
                "content": "该请求不是外部执行挂起，请使用 permission confirm 接口。",
            }
            return

        if user_info:
            quota_block = await self._quota_block_message(user_info)
            if quota_block:
                yield {
                    "type": "error",
                    "status": "quota_exceeded",
                    "content": quota_block,
                    "trace_id": pending.trace_id,
                }
                return

        runner = self._build_agentscope_runner_from_pending(pending, user_info=user_info)
        await self._restore_runner_execution_context(
            runner,
            pending,
            user_info=user_info,
        )
        execution_results = self._build_external_execution_results(results)

        yield {
            "type": "external_execution_result",
            "status": "success",
            "external_execution_request_id": external_execution_request_id,
            "tool_call_id": getattr(pending.tool_call, "id", None),
        }

        full_response_content = ""
        execution_status = "success"
        start_time = asyncio.get_running_loop().time()
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        lane_user_id = current_user_id or pending.user_id

        try:
            async with conversation_run_lane.hold(
                user_id=lane_user_id,
                conversation_id=conversation_id,
                trace_id=pending.trace_id,
            ):
                async for chunk in runner.resume_agentscope_external_execution(
                    pending,
                    execution_results=execution_results,
                ):
                    full_response_content = _accumulate_stream_content(full_response_content, chunk)
                    if chunk.get("status") == "error":
                        execution_status = "error"
                    yield chunk
        except ConversationRunBusyError:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }
            return

        p_tokens, c_tokens, t_tokens = 0, 0, 0
        trace_buffer = runner.trace_buffer
        try:
            from app.services.ai.audit import aggregate_tokens_from_trace_buffer
            p_tokens, c_tokens, t_tokens = aggregate_tokens_from_trace_buffer(trace_buffer) if trace_buffer else (0, 0, 0)
        except Exception as agg_err:
            logger.warning(f"Failed to aggregate tokens after external resume: {agg_err}")

        if p_tokens or c_tokens:
            yield {
                "type": "meta",
                "prompt_tokens": p_tokens,
                "completion_tokens": c_tokens,
                "total_tokens": t_tokens,
            }

        agent_config = runner.config
        conversation_id = runner.conversation_id or pending.snapshot.conversation_id
        user_query = (pending.state or {}).get("user_query") or ""

        if conversation_id and full_response_content:
            u_id = user_info.get("user_id") if user_info else pending.user_id
            handled_by = getattr(agent_config, "agent_name", None) if agent_config else None
            asyncio.create_task(memory_service.add_message(
                u_id,
                conversation_id,
                "assistant",
                full_response_content,
                trace_id=pending.trace_id,
                agent_name=handled_by,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
            ))

        duration = (asyncio.get_running_loop().time() - start_time) * 1000
        asyncio.create_task(AuditManager.log_transaction(
            pending.trace_id,
            agent_config,
            user_query,
            full_response_content,
            user_info,
            execution_status,
            duration,
            trace_buffer,
            conversation_id=conversation_id,
        ))

    async def _execute_multi_agent(
        self,
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
        session_turn=None,
        route_hints: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes primary and secondary agents in parallel and yields combined results.
        """
        from app.services.ai.turn_classifier import turn_type_label

        if session_turn:
            tc, _, tc_elapsed = session_turn
            yield {
                "type": "log",
                "title": "分析用户请求并进行意图识别",
                "details": f"{turn_type_label(tc.turn_type)}。{tc.reasoning}",
                "status": "success",
                "category": "intent",
                "turn_type": tc.turn_type.value,
                "execution_time_ms": tc_elapsed,
            }

        # 1. Resolve Secondary Configs
        secondary_configs = []
        async with AsyncSessionLocal() as session:
            for s_id in secondary_agent_ids:
                s_config = await AgentManagerService.get_active_agent_config(session, agent_id=s_id)
                if s_config:
                    secondary_configs.append(s_config)

        # 2. Setup Executors
        all_configs = [primary_config] + secondary_configs
        executors = []
        for config in all_configs:
            exec = await AgentDispatcher.dispatch(
                config,
                user_query,
                messages,
                trace_id,
                trace_buffer,
                debug_options,
                permission_options,
                user_info,
                conversation_id,
                shared_turn=session_turn,
                route_hints=route_hints,
            )
            executors.append(exec)

        yield {
            "type": "log",
            "title": "多智能体协作",
            "details": f"正在并行调度 {len(executors)} 个专家智能体: " + ", ".join([c.agent_name for c in all_configs]),
            "status": "success"
        }

        # 3. Parallel Execution with Queue-based log streaming
        queue = asyncio.Queue()

        async def run_executor(executor, config):
            full_text = ""
            try:
                # We need a clean copy of messages for each executor as they might modify it?
                # Actually most executors just read it.
                async for chunk in executor.execute(messages):
                    if "content" in chunk:
                        full_text += chunk["content"]
                    elif "type" in chunk and chunk["type"] in ["log", "router_log"]:
                        # Prefix log title with Agent Name to identify the source
                        if "title" in chunk:
                             chunk["title"] = f"[{config.agent_name}] {chunk['title']}"
                        await queue.put(chunk)
                    elif "type" in chunk and chunk["type"] == "thinking":
                        # Forward thinking status (might overlap, but SSE handles it)
                        await queue.put(chunk)
            except Exception as e:
                logger.error(f"Error in multi-agent sub-task ({config.agent_name}): {e}", exc_info=True)
                await queue.put({
                    "type": "log",
                    "title": f"[{config.agent_name}] 执行异常",
                    "details": str(e),
                    "status": "error"
                })
                full_text = f"【{config.agent_name} 执行失败】: {str(e)}"
            return {"name": config.agent_name, "content": full_text}

        # Start all tasks
        tasks = [asyncio.create_task(run_executor(exec, conf)) for exec, conf in zip(executors, all_configs)]
        results_task = asyncio.gather(*tasks)

        # Stream logs while tasks are running
        while not results_task.done() or not queue.empty():
            try:
                # Use wait_for to check done status frequently
                chunk = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield chunk
                queue.task_done()
            except (asyncio.TimeoutError, asyncio.QueueEmpty):
                if results_task.done() and queue.empty():
                    break
                await asyncio.sleep(0.01)

        agent_outputs = await results_task

        # 4. Final Synthesis
        yield {
            "type": "log",
            "title": "结果聚合",
            "details": "正在汇总各专家意见并组织最终回答...",
            "status": "success"
        }

        async for chunk in self._synthesize_multi_agent_results(
            primary_config, user_query, agent_outputs, trace_buffer
        ):
            yield chunk

    async def _synthesize_multi_agent_results(
        self,
        config: ChatConfig,
        user_query: str,
        agent_outputs: List[Dict[str, str]],
        trace_buffer: List[AgentExecutionStep],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Synthesizes multiple agent outputs into a unified response.
        """
        outputs_str = ""
        for out in agent_outputs:
            outputs_str += f"### 专家智能体: {out['name']}\n{out['content']}\n\n"

        system_prompt = AgentServicePrompts.MULTI_AGENT_SYNTHESIS_SYSTEM

        human_content = AgentServicePrompts.multi_agent_synthesis_human(user_query, outputs_str)

        # Use synthesis model from primary agent config
        llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=config)

        lc_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=human_content)
        ]

        start_synthesis = time.time()
        full_content = ""
        accumulated_msg = None
        async for chunk in llm.astream(lc_messages):
            if accumulated_msg is None:
                accumulated_msg = chunk
            else:
                accumulated_msg += chunk
            if chunk.content:
                full_content += chunk.content
                yield {"content": chunk.content}

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

    def __init__(self):
        pass

agent_service = AgentService()
