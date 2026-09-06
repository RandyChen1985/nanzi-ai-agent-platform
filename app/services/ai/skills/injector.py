"""SkillInjector: 负责运行时技能匹配、自动扫描、首轮门禁与指令提示词注入。"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Set

from app.core.config import settings
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class SkillInjector:
    """Encapsulates skill matching, scanning, policy resolution and prompt injection."""

    USING_SUPERPOWERS_SKILL_ID = "using-superpowers"

    @staticmethod
    def _parse_bool_config(value: Any, default: bool) -> bool:
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _parse_int_config(
        value: Any, default: int, *, min_value: int, max_value: int | None = None
    ) -> int:
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

    @classmethod
    async def resolve_skill_full_load_policy(cls) -> Dict[str, Any]:
        (
            enabled_raw,
            min_score_raw,
            max_count_raw,
            max_bytes_raw,
        ) = await asyncio.gather(
            ConfigService.get("skill_auto_full_load_enabled", "true"),
            ConfigService.get("skill_auto_full_load_min_score", "0.75"),
            ConfigService.get("skill_auto_full_load_max_count", "1"),
            ConfigService.get("skill_auto_full_load_max_bytes", "65536"),
        )
        return {
            "enabled": cls._parse_bool_config(enabled_raw, True),
            "min_score": cls._parse_float_config(min_score_raw, 0.75),
            "max_count": cls._parse_int_config(max_count_raw, 1, min_value=0, max_value=3),
            "max_bytes": cls._parse_int_config(
                max_bytes_raw, 65536, min_value=1024, max_value=262144
            ),
        }

    @staticmethod
    def should_preload_skill_full_instruction(
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
    def is_new_session_first_user_turn(messages: Optional[List[Dict[str, Any]]]) -> bool:
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
    def should_force_preload_scanned_skill(
        cls,
        *,
        skill_id: str,
        messages: Optional[List[Dict[str, Any]]],
    ) -> bool:
        return (
            skill_id == cls.USING_SUPERPOWERS_SKILL_ID
            and cls.is_new_session_first_user_turn(messages)
        )

    @classmethod
    def ensure_first_turn_superpowers_candidate(
        cls,
        *,
        scanned_skills: List[Dict[str, Any]],
        available_skills: List[Dict[str, Any]],
        messages: Optional[List[Dict[str, Any]]],
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Ensure using-superpowers is considered on the first user turn (any agent)."""
        if not cls.is_new_session_first_user_turn(messages):
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

    @classmethod
    def append_first_turn_superpowers(
        cls,
        *,
        messages: Optional[List[Dict[str, Any]]],
        agent_config: Any,
        user_info: Optional[Dict[str, Any]],
        skills_injection: List[str],
        mounted_skill_ids: Set[str],
        full_load_policy: Dict[str, Any],
        full_loaded_count: int,
        skills_log_callback: Optional[Callable] = None,
    ) -> int:
        """所有智能体：新会话首轮强制预载 using-superpowers（完整指令）。"""
        if not cls.is_new_session_first_user_turn(messages):
            return full_loaded_count
        if cls.USING_SUPERPOWERS_SKILL_ID in mounted_skill_ids:
            return full_loaded_count

        from app.services.ai.skill_resolver import (
            list_skill_metas,
            load_skill_md_content,
            skill_filter_kwargs_from_config,
        )

        skill_filter = skill_filter_kwargs_from_config(agent_config)
        available_skills = list_skill_metas(user_info=user_info, **skill_filter)
        skill_meta = next(
            (
                skill
                for skill in available_skills
                if skill.get("id") == cls.USING_SUPERPOWERS_SKILL_ID
            ),
            None,
        )
        # 首轮门禁：即便 skills_custom 白名单未包含，也尽量从全局技能库加载
        if skill_meta is None and skill_filter.get("skills_custom"):
            available_skills = list_skill_metas(
                user_info=user_info,
                skills_custom=False,
                allowed_global_skills=None,
            )
            skill_meta = next(
                (
                    skill
                    for skill in available_skills
                    if skill.get("id") == cls.USING_SUPERPOWERS_SKILL_ID
                ),
                None,
            )
        if not skill_meta:
            return full_loaded_count

        skill_id = cls.USING_SUPERPOWERS_SKILL_ID
        skill_name = skill_meta.get("name") or skill_id
        description = skill_meta.get("description") or ""
        full_instruction = load_skill_md_content(
            skill_id,
            max_bytes=int(full_load_policy["max_bytes"]),
            user_info=user_info,
            scope=skill_meta.get("scope"),
            skill_md_path=skill_meta.get("skill_md_path"),
        )
        if full_instruction:
            full_loaded_count += 1
        skills_injection.append(
            cls.build_skill_injection(
                skill_name=skill_name,
                skill_id=skill_id,
                description=description,
                full_instruction=full_instruction,
            )
        )
        mounted_skill_ids.add(skill_id)
        logger.info(
            "[Skills] First-turn gate preloaded %s for agent=%s (%s).",
            skill_id,
            getattr(agent_config, "agent_id", None) or getattr(agent_config, "agent_name", None),
            "full instruction" if full_instruction else "summary only",
        )
        if skills_log_callback:
            if full_instruction:
                details_msg = (
                    f"新会话首轮门禁已强制启用；已预载「{skill_name}」(ID: {skill_id}) "
                    "完整 SKILL.md 指令，本轮可直接按该流程执行。"
                )
            else:
                details_msg = (
                    f"新会话首轮门禁已启用「{skill_name}」(ID: {skill_id})，"
                    "但未能读取完整指令；模型须调用 read_skill_instruction。"
                )
            skills_log_callback(skill_id, skill_name, details_msg)
        return full_loaded_count

    @staticmethod
    def build_skill_injection(
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
    def build_skill_log_chunk(skill_id: str, skill_name: str, details_msg: str) -> Dict[str, Any]:
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

    @classmethod
    async def inject_skills(
        cls,
        *,
        messages: List[Dict[str, Any]],
        user_query: str,
        agent_config: Any,
        user_info: Optional[Dict[str, Any]] = None,
        skills_log_callback: Optional[Callable] = None,
        resource_scope: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """挂载与自动匹配技能，返回 skills_injection。"""
        active_skills = []
        if messages and "files" in messages[-1] and messages[-1]["files"]:
            for file_obj in messages[-1]["files"]:
                if file_obj.get("type") == "skill":
                    active_skills.append(file_obj)

        scoped_skill_items = [
            item
            for item in (resource_scope or {}).get("skills", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if scoped_skill_items:
            scoped_ids = {str(item["id"]) for item in scoped_skill_items}
            active_skills = [
                skill for skill in active_skills if str(skill.get("url") or "") in scoped_ids
            ]
            mounted_ids = {str(item.get("url") or "") for item in active_skills}
            active_skills.extend(
                {
                    "type": "skill",
                    "url": str(item["id"]),
                    "filename": item.get("name") or str(item["id"]),
                    "skillMeta": item,
                }
                for item in scoped_skill_items
                if str(item["id"]) not in mounted_ids
            )

        mounted_skill_ids = {s.get("url") for s in active_skills if s.get("url")}
        skills_injection = []
        full_load_policy = await cls.resolve_skill_full_load_policy()
        full_loaded_count = 0

        if active_skills:
            from app.services.ai.skill_resolver import (
                get_user_personal_skills_dir,
                load_skill_md_content,
            )
            from app.utils.skill_metadata import parse_skill_frontmatter

            for skill_obj in active_skills:
                skill_id = skill_obj.get("url")
                if not skill_id:
                    continue
                meta_override = skill_obj.get("skillMeta") or skill_obj.get("skill_meta")
                skill_scope = None
                explicit_skill_md_path = None
                if meta_override and isinstance(meta_override, dict):
                    skill_name = str(meta_override.get("name") or skill_id)
                    description = str(meta_override.get("description") or "")
                    skill_scope = str(meta_override.get("scope") or "").strip().lower() or None
                    explicit_skill_md_path = meta_override.get("skill_md_path") or meta_override.get(
                        "skillMdPath"
                    )
                else:
                    skill_name = skill_obj.get("filename", skill_id).replace(" (技能)", "")
                    description = ""

                skill_scope = skill_scope or str(skill_obj.get("scope") or "").strip().lower() or None
                candidate_paths: list[str] = []
                if explicit_skill_md_path:
                    candidate_paths.append(str(explicit_skill_md_path))
                if skill_scope == "personal":
                    personal_dir = get_user_personal_skills_dir(user_info)
                    if personal_dir:
                        candidate_paths.append(os.path.join(personal_dir, skill_id, "SKILL.md"))
                candidate_paths.append(os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md"))

                skill_md_path = next(
                    (p for p in candidate_paths if os.path.exists(p)), candidate_paths[-1]
                )
                if not (meta_override and isinstance(meta_override, dict)) and os.path.exists(
                    skill_md_path
                ):
                    meta = parse_skill_frontmatter(skill_id, skill_md_path)
                    skill_name = meta.get("name") or skill_obj.get("filename", skill_id).replace(
                        " (技能)", ""
                    )
                    description = meta.get("description") or ""
                elif not (meta_override and isinstance(meta_override, dict)):
                    logger.warning("[Skills] Skill markdown not found at %s", skill_md_path)

                full_instruction = None
                if cls.should_preload_skill_full_instruction(
                    match_source="mounted",
                    policy=full_load_policy,
                    loaded_count=full_loaded_count,
                ):
                    full_instruction = load_skill_md_content(
                        skill_id,
                        max_bytes=int(full_load_policy["max_bytes"]),
                        user_info=user_info,
                        scope=skill_scope,
                        skill_md_path=skill_md_path if os.path.exists(skill_md_path) else None,
                    )
                    if full_instruction:
                        full_loaded_count += 1

                skills_injection.append(
                    cls.build_skill_injection(
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

        if user_query and not scoped_skill_items:
            try:
                from app.services.ai.skill_resolver import (
                    load_skill_md_content,
                    resolve_skills_from_query,
                    skill_filter_kwargs_from_config,
                )

                skill_filter = skill_filter_kwargs_from_config(agent_config)
                for skill_meta in resolve_skills_from_query(
                    user_query,
                    user_info=user_info,
                    **skill_filter,
                ):
                    skill_id = skill_meta.get("id")
                    if not skill_id or skill_id in mounted_skill_ids:
                        continue
                    skill_name = skill_meta.get("name") or skill_id
                    description = skill_meta.get("description") or ""
                    full_instruction = None
                    if cls.should_preload_skill_full_instruction(
                        match_source=str(skill_meta.get("match_source") or "mention"),
                        match_score=skill_meta.get("match_score"),
                        policy=full_load_policy,
                        loaded_count=full_loaded_count,
                    ):
                        full_instruction = load_skill_md_content(
                            skill_id,
                            max_bytes=int(full_load_policy["max_bytes"]),
                            user_info=user_info,
                            scope=skill_meta.get("scope"),
                            skill_md_path=skill_meta.get("skill_md_path"),
                        )
                        if full_instruction:
                            full_loaded_count += 1
                    skills_injection.append(
                        cls.build_skill_injection(
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
                    skill_filter_kwargs_from_config,
                )

                if is_main_general_agent(agent_config):
                    skill_filter = skill_filter_kwargs_from_config(agent_config)
                    scan_enabled_raw = await ConfigService.get("skill_auto_scan_enabled", "true")
                    scan_enabled = str(scan_enabled_raw or "true").strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                    }
                    if scan_enabled:
                        min_score_raw = await ConfigService.get(
                            "skill_auto_scan_min_score", "0.45"
                        )
                        try:
                            min_score = float(min_score_raw) if min_score_raw is not None else 0.45
                        except (TypeError, ValueError):
                            min_score = 0.45
                        max_results_raw = await ConfigService.get(
                            "skill_auto_scan_max_results", "1"
                        )
                        try:
                            max_scan_results = (
                                int(max_results_raw) if max_results_raw is not None else 1
                            )
                        except (TypeError, ValueError):
                            max_scan_results = 1
                        max_scan_results = max(1, min(max_scan_results, 3))

                        scanned_skills = []
                        if should_scan_skills_for_query(user_query):
                            scanned_skills = scan_relevant_skills(
                                user_query,
                                user_info=user_info,
                                exclude_ids=mounted_skill_ids,
                                max_results=max_scan_results,
                                min_score=min_score,
                                **skill_filter,
                            )
                        available_skills = list_skill_metas(
                            user_info=user_info,
                            **skill_filter,
                        )
                        scanned_skills = cls.ensure_first_turn_superpowers_candidate(
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
                            force_full_instruction = cls.should_force_preload_scanned_skill(
                                skill_id=skill_id,
                                messages=messages,
                            )
                            if force_full_instruction or cls.should_preload_skill_full_instruction(
                                match_source=str(skill_meta.get("match_source") or "scan"),
                                match_score=match_score,
                                policy=full_load_policy,
                                loaded_count=full_loaded_count,
                            ):
                                full_instruction = load_skill_md_content(
                                    skill_id,
                                    max_bytes=int(full_load_policy["max_bytes"]),
                                    user_info=user_info,
                                    scope=skill_meta.get("scope"),
                                    skill_md_path=skill_meta.get("skill_md_path"),
                                )
                                if full_instruction:
                                    full_loaded_count += 1
                            skills_injection.append(
                                cls.build_skill_injection(
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
                                "full instruction preloaded"
                                if full_instruction
                                else "summary only",
                            )
                            if skills_log_callback:
                                score_hint = (
                                    f"（相关度 {match_score}）"
                                    if match_score is not None
                                    else ""
                                )
                                if full_instruction:
                                    force_hint = (
                                        "新会话首轮门禁已强制启用；"
                                        if force_full_instruction
                                        else ""
                                    )
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

        # 所有智能体：新会话首轮强制预载 using-superpowers（主助手扫描路径若已注入则跳过）
        try:
            full_loaded_count = cls.append_first_turn_superpowers(
                messages=messages,
                agent_config=agent_config,
                user_info=user_info,
                skills_injection=skills_injection,
                mounted_skill_ids=mounted_skill_ids,
                full_load_policy=full_load_policy,
                full_loaded_count=full_loaded_count,
                skills_log_callback=skills_log_callback,
            )
        except Exception as first_turn_err:
            logger.warning(
                "[Skills] Failed to preload first-turn using-superpowers: %s",
                first_turn_err,
            )

        if skills_injection:
            MAX_PRELOAD_SKILLS = 5
            if len(skills_injection) > MAX_PRELOAD_SKILLS:
                logger.info(
                    f"[Skills] Too many skills ({len(skills_injection)}), truncating to top {MAX_PRELOAD_SKILLS}"
                )
                skills_injection = skills_injection[:MAX_PRELOAD_SKILLS]
                skills_injection.append(
                    "=== [已截断] 系统中已挂载或解析出更多可用技能，出于上下文性能优化，其余技能摘要未全部载入。如有需要，模型应通过调用 list_available_skills 工具获取其余技能详细摘要 ==="
                )

        # 统计激活情况
        if mounted_skill_ids:
            try:
                from app.services.ai.skills_stats_service import skills_stats_service
                await skills_stats_service.record_activations(mounted_skill_ids)
            except Exception as stats_err:
                logger.error(f"[SkillsStats] Auto-recording skill activations failed: {stats_err}")

        return skills_injection
