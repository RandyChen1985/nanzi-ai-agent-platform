"""SkillInjector: 负责运行时技能匹配、自动扫描与指令提示词注入。"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.config_service import ConfigService
# 技能来源口径（scope）的唯一解析/判定入口：挂载项属于个人还是公共，全平台只此一份口径。
from app.utils.skill_metadata import (
    extract_plain_user_query,
    is_personal_skill_attachment,
    is_personal_skill_scope,
    normalize_skill_scope,
)

logger = logging.getLogger(__name__)

# 单次挂载 / 口头点名技能的紧邻追问延续缓存（进程内轻量 LRU + 短 TTL，不写库）。
_SESSION_SKILL_FOLLOWUP_TTL_SECONDS = 1800.0
_SESSION_SKILL_FOLLOWUP_MAX_ENTRIES = 512
_recent_session_skills: "OrderedDict[str, Tuple[float, Tuple[str, ...]]]" = OrderedDict()
_recent_session_skills_lock = threading.RLock()

_SKILL_FOLLOWUP_SIGNALS = (
    "刚才", "刚刚", "上面", "上一轮", "上轮", "按这个", "按刚才", "按上面",
    "照这个", "照刚才", "继续", "接着", "再跑", "再执行", "再来一次", "重跑",
    "下一步", "第二步", "第三步", "换成", "改成", "再按",
)


def _session_skill_cache_key(
    user_info: Optional[Dict[str, Any]], conversation_id: Optional[str]
) -> Optional[str]:
    conv = str(conversation_id or "").strip()
    if not conv:
        return None
    uid = ""
    if isinstance(user_info, dict):
        for key in ("u_id_val", "id", "user_id", "uid"):
            raw = user_info.get(key)
            if raw is not None and str(raw).strip():
                uid = str(raw).strip()
                break
    return f"{uid or 'anon'}|{conv}"


def remember_recent_session_skills(
    user_info: Optional[Dict[str, Any]],
    conversation_id: Optional[str],
    skill_ids: List[str],
) -> None:
    key = _session_skill_cache_key(user_info, conversation_id)
    cleaned = tuple(dict.fromkeys(str(s).strip() for s in (skill_ids or []) if str(s).strip()))
    if not key or not cleaned:
        return
    with _recent_session_skills_lock:
        _recent_session_skills.pop(key, None)
        _recent_session_skills[key] = (time.monotonic(), cleaned)
        while len(_recent_session_skills) > _SESSION_SKILL_FOLLOWUP_MAX_ENTRIES:
            _recent_session_skills.popitem(last=False)


def get_recent_session_skills(
    user_info: Optional[Dict[str, Any]],
    conversation_id: Optional[str],
) -> Tuple[str, ...]:
    key = _session_skill_cache_key(user_info, conversation_id)
    if not key:
        return ()
    now = time.monotonic()
    with _recent_session_skills_lock:
        entry = _recent_session_skills.get(key)
        if entry is None:
            return ()
        ts, ids = entry
        if now - ts > _SESSION_SKILL_FOLLOWUP_TTL_SECONDS:
            _recent_session_skills.pop(key, None)
            return ()
        _recent_session_skills.move_to_end(key)
        return ids


def clear_recent_session_skills_cache() -> None:
    """清空会话技能追问缓存（测试用）。"""
    with _recent_session_skills_lock:
        _recent_session_skills.clear()


def _looks_like_skill_followup_query(query: str) -> bool:
    q = (query or "").strip()
    if not q or len(q) > 80:
        return False
    from app.services.ai.intent_service import (
        looks_like_general_followup,
        looks_like_greeting,
        looks_like_meta_action,
    )

    if looks_like_greeting(q) or looks_like_meta_action(q):
        return False
    if looks_like_general_followup(q):
        return True
    q_lower = q.lower()
    return any(sig in q_lower for sig in _SKILL_FOLLOWUP_SIGNALS)


class SkillInjector:
    """Encapsulates skill matching, scanning, policy resolution and prompt injection."""

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
            # 上限与上下文侧 ``DEFAULT_TOOL_OUTPUT_MAX_LEN``（64Ki 字符）对齐：预载进
            # system prompt 的全文不经过工具结果裁剪，配置成 262144 只会把超长正文
            # 塞满上下文，而模型对它的可见度并不比 64Ki 更多。
            "max_bytes": cls._parse_int_config(
                max_bytes_raw, 65536, min_value=1024, max_value=65536
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
        # 用户可见文案统一称「技能」（旧版叫「流程」，已落库的历史快照仍可能带旧措辞，
        # 前端 skillFlowBadges 两套前缀都认）。
        details = details_msg or (
            f"已识别候选技能「{skill_name}」(ID: {skill_id})。"
            "当前仅加载技能摘要；若本轮确需执行，系统会读取完整技能说明后再处理。"
        )
        is_full_enabled = (
            "已预载完整" in details
            or "可直接按该技能执行" in details
            # 旧措辞：历史调用方/测试替身仍可能传「流程」，完成态判定不能跟着文案漂移。
            or "可直接按该流程执行" in details
        )
        if is_full_enabled:
            return {
                "type": "log",
                "id": f"skill_enabled_{skill_id}",
                "title": f"已启用技能: {skill_name}",
                "details": details,
                "status": "success",
            }

        user_facing_details = details
        if "read_skill_instruction" in user_facing_details:
            user_facing_details = (
                f"已识别候选技能「{skill_name}」(ID: {skill_id})。"
                "当前仅加载技能摘要；若本轮确需执行，系统会读取完整技能说明后再处理。"
            )
        return {
            "type": "log",
            "id": f"skill_candidate_{skill_id}",
            "title": f"已识别候选技能: {skill_name}",
            "details": user_facing_details,
            "status": "success",
        }

    @classmethod
    def resolve_mount_meta(
        cls,
        item: Dict[str, Any],
        skill_id: str,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """公开挂载元数据解析入口（优先命中技能目录指纹缓存，未命中再回退单文件读盘）。"""
        meta = item.get("skillMeta") or item.get("skill_meta")
        if isinstance(meta, dict) and "enabled" in meta:
            return meta
        try:
            from app.services.ai.skill_resolver import (
                SCOPE_GLOBAL,
                SCOPE_PERSONAL,
                get_user_personal_skills_dir,
                scan_skill_dir_cached,
            )

            if is_personal_skill_attachment(item):
                from app.utils.context import current_user_info

                personal_dir = get_user_personal_skills_dir(
                    user_info or current_user_info.get()
                )
                if personal_dir:
                    for cached in scan_skill_dir_cached(
                        personal_dir, SCOPE_PERSONAL, allow_seed=True
                    ):
                        if cached.get("id") == skill_id:
                            return cached
            else:
                for cached in scan_skill_dir_cached(settings.SKILLS_DIR, SCOPE_GLOBAL):
                    if cached.get("id") == skill_id:
                        return cached
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Skills] 缓存读取挂载技能元数据失败 %s: %s", skill_id, exc)
        return cls._read_skill_meta_for_mount(item, skill_id, user_info)

    @classmethod
    def drop_disabled_skill_items(
        cls,
        items: Optional[List[Dict[str, Any]]],
        user_info: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """丢弃 frontmatter 里 ``enabled`` 判定为禁用的挂载项。

        口径统一走 :func:`app.utils.skill_metadata.is_skill_enabled`：此前挂载这条路
        完全不看 enabled，只拦 forbidden，于是「管理页显示已禁用、挂载后照样整份注入」。
        取不到元数据时按**启用**处理——保守方向：不因读盘失败而误丢用户显式挂载的技能。

        ``user_info`` 透传给元数据读取：个人技能的目录由用户身份推导，缺了它只能退回请求
        上下文（pipeline 早期阶段不一定有），会读不到个人技能的 frontmatter。
        """
        from app.utils.skill_metadata import is_skill_enabled

        kept: List[Dict[str, Any]] = []
        for item in items or []:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            skill_id = str(item.get("url") or "").strip()
            if not skill_id:
                kept.append(item)
                continue
            meta = cls.resolve_mount_meta(item, skill_id, user_info)
            if not is_skill_enabled(meta):
                logger.warning(
                    "[Skills] 技能 %s 已被禁用（enabled=false），本轮回合不再挂载", skill_id
                )
                continue
            kept.append(item)
        return kept

    @staticmethod
    def _read_skill_meta_for_mount(
        item: Dict[str, Any],
        skill_id: str,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """为挂载项读取磁盘 frontmatter（个人技能优先，其次公共技能库），失败返回空字典。

        来源判定走 :func:`is_personal_skill_attachment`：scope 可能只写在附件顶层
        （``ChatFile.scope``，聊天接口已补回该字段），也可能只写在 ``skillMeta`` 里，
        两处都要认。此前只看顶层，个人技能会被当成公共技能去读平台库的同名 SKILL.md。

        个人目录由用户身份推导（``user_info`` 优先，其次请求上下文 ``current_user_info``）：
        早先这里从 ``app.core.context`` 导入 ``current_user_info``，而那个模块没有这个名字，
        ImportError 被下面的 except 吞掉——个人技能这条分支从未真正读过盘。
        """
        from app.utils.skill_metadata import parse_skill_frontmatter

        candidates: List[str] = []
        if is_personal_skill_attachment(item):
            try:
                from app.services.ai.skill_resolver import (
                    get_user_personal_skills_dir,
                    resolve_personal_skill_dir,
                )
                from app.utils.context import current_user_info

                personal_dir = get_user_personal_skills_dir(user_info or current_user_info.get())
                if personal_dir:
                    resolved = resolve_personal_skill_dir(personal_dir, skill_id)
                    candidates.append(
                        os.path.join(
                            resolved or os.path.join(personal_dir, skill_id), "SKILL.md"
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[Skills] 读取个人技能元数据失败 %s: %s", skill_id, exc)
        else:
            candidates.append(os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md"))
        for candidate in candidates:
            if os.path.exists(candidate):
                try:
                    return parse_skill_frontmatter(skill_id, candidate)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[Skills] 解析技能元数据失败 %s: %s", skill_id, exc)
        return {}

    @classmethod
    def drop_disallowed_custom_skill_items(
        cls,
        items: Optional[List[Dict[str, Any]]],
        agent_config: Any,
    ) -> List[Dict[str, Any]]:
        """智能体开启 ``skills_custom=True`` 时，丢弃不在白名单内的公共技能挂载项。

        个人技能（scope=personal）始终放行，与口头点名 / 自动扫描 / ``list_available_skills`` /
        ``read_skill_instruction`` / 沙箱播种保持完全一致的白名单口径。
        """
        from app.services.ai.skill_resolver import skill_filter_kwargs_from_config

        skill_filter = skill_filter_kwargs_from_config(agent_config)
        if not skill_filter.get("skills_custom"):
            return list(items or [])

        allowlist = set(skill_filter.get("allowed_global_skills") or [])
        kept: List[Dict[str, Any]] = []
        dropped: List[str] = []
        for item in items or []:
            if not isinstance(item, dict) or item.get("type") != "skill":
                kept.append(item)
                continue
            skill_id = str(item.get("url") or "").strip()
            if not skill_id:
                kept.append(item)
                continue
            if is_personal_skill_attachment(item):
                kept.append(item)
                continue
            if skill_id not in allowlist:
                dropped.append(skill_id)
                continue
            kept.append(item)
        if dropped:
            logger.warning(
                "[Skills] 智能体已启用自定义技能白名单，已拦挂载白名单外公共技能: %s",
                ", ".join(dropped),
            )
        return kept

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
        conversation_id: Optional[str] = None,
    ) -> List[str]:
        """挂载与自动匹配技能，返回 skills_injection。"""
        clean_user_query = extract_plain_user_query(user_query)

        explicit_message_skill_ids: Set[str] = set()
        active_skills = []
        if messages and "files" in messages[-1] and messages[-1]["files"]:
            for file_obj in messages[-1]["files"]:
                if file_obj.get("type") == "skill":
                    active_skills.append(file_obj)
                    if file_obj.get("url"):
                        explicit_message_skill_ids.add(str(file_obj.get("url")))

        scoped_skill_items = [
            item
            for item in (resource_scope or {}).get("skills", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if scoped_skill_items:
            scoped_ids = {str(item["id"]) for item in scoped_skill_items}
            dropped_by_scope = [
                str(skill.get("url") or "")
                for skill in active_skills
                if str(skill.get("url") or "") not in scoped_ids
            ]
            if dropped_by_scope:
                logger.warning(
                    "[Skills] 会话固定技能范围（resource_scope.skills）已生效，已忽略范围外的本轮显式挂载技能: %s",
                    ", ".join(dropped_by_scope),
                )
            active_skills = [
                {**skill, "_from_resource_scope": True}
                for skill in active_skills
                if str(skill.get("url") or "") in scoped_ids
            ]
            mounted_ids = {str(item.get("url") or "") for item in active_skills}
            active_skills.extend(
                {
                    "type": "skill",
                    "url": str(item["id"]),
                    "filename": item.get("name") or str(item["id"]),
                    # 会话固定资源（resource_scope.skills）也带来源标记，且必须**显式**抬到附件
                    # 顶层：下游（drop_forbidden_skill_items / _read_skill_meta_for_mount /
                    # 挂载正文解析）都按 scope 决定"这份技能属于谁"。丢了这一层，与公共技能
                    # 同名的个人技能会被公共禁用集合连坐、并按公共库去解析 id。
                    "scope": normalize_skill_scope(
                        item.get("scope"), source="resource_scope.skills.scope"
                    ),
                    "skillMeta": item,
                    "_from_resource_scope": True,
                }
                for item in scoped_skill_items
                if str(item["id"]) not in mounted_ids
            )

        # 权限侧禁用（forbidden_skills）必须在挂载这条路上也拦：用户绕过技能菜单直接指定
        # 一个被禁技能时，过滤只能发生在这里。本轮只查一次，后续点名/扫描/追问复用。
        from app.services.ai.skills.policy import (
            drop_forbidden_skill_items,
            get_forbidden_skill_ids,
        )

        forbidden_skill_ids = await get_forbidden_skill_ids()
        active_skills = drop_forbidden_skill_items(active_skills, forbidden_skill_ids)

        # 智能体自定义技能白名单（skills_custom=True）同样必须在挂载与会话固定路径上拦住，
        # 避免绕过白名单直接预载公共技能正文。
        active_skills = cls.drop_disallowed_custom_skill_items(active_skills, agent_config)

        # enabled=false 在这条路上也必须拦。显式挂载（输入框技能 chip）与会话固定资源
        # （resource_scope.skills，服务端持久化、跨轮生效）都不经过扫描侧的门禁：
        # 少了这一道，「管理页点禁用」对已挂载/已固定的会话完全无效，正文照旧整份注入。
        active_skills = cls.drop_disabled_skill_items(active_skills, user_info)

        mounted_skill_ids = {s.get("url") for s in active_skills if s.get("url")}
        # 真正被本轮激活的技能集合（用于统计与会话追问延续）：
        # 显式挂载、口头点名、以及高置信预载全文的扫描技能计入激活；仅摘要候选不计入。
        activated_skill_ids: List[str] = []
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
                    skill_scope = normalize_skill_scope(
                        meta_override.get("scope"), source="skillMeta.scope"
                    )
                    explicit_skill_md_path = meta_override.get("skill_md_path") or meta_override.get(
                        "skillMdPath"
                    )
                else:
                    skill_name = skill_obj.get("filename", skill_id).replace(" (技能)", "")
                    description = ""

                # 顶层附件字段是历史消息里更稳的那一份（skillMeta 可能被前端裁剪过），
                # 两处都归一化到同一套取值，避免"scope 只写在某一层时被判成公共技能"。
                skill_scope = skill_scope or normalize_skill_scope(
                    skill_obj.get("scope"), source="ChatFile.scope"
                )
                # 但传给读取器（load_skill_md_content）的 scope 只保留"个人"标记：它把
                # "非空且非 personal"的 scope 当成过滤条件（meta.scope 不等就跳过候选），
                # 而公共技能在 meta 里的口径写作 "global"，直接把 "public" 透传会把
                # meta_path 候选全部过滤掉。既有调用点（点名/扫描路径）传的也是 global 或
                # personal，这里按"个人 or 未指定"折算，保持与改动前完全一致的读取行为。
                read_scope = skill_scope if is_personal_skill_scope(skill_scope) else None
                candidate_paths: list[str] = []
                if explicit_skill_md_path:
                    candidate_paths.append(str(explicit_skill_md_path))
                if skill_scope == "personal":
                    personal_dir = get_user_personal_skills_dir(user_info)
                    if personal_dir:
                        # 兼容历史遗留的 .seed 布局：否则个人技能正文读不到。
                        from app.services.ai.skill_resolver import resolve_personal_skill_dir

                        resolved_dir = resolve_personal_skill_dir(personal_dir, skill_id)
                        candidate_paths.append(
                            os.path.join(
                                resolved_dir or os.path.join(personal_dir, skill_id),
                                "SKILL.md",
                            )
                        )
                else:
                    # 只有非个人技能才允许落到公共技能库。此前两者共用一条候选链，于是
                    # 「个人技能目录里没有 SKILL.md（幽灵/上传中断/被并发删除）而公共库
                    # 恰有同 ID」时，会把**公共**技能正文配着个人技能的名字注入进去，
                    # 模型按错误的技能执行——宁可注入不到，也不能张冠李戴。
                    candidate_paths.append(
                        os.path.join(settings.SKILLS_DIR, skill_id, "SKILL.md")
                    )

                skill_md_path = next(
                    (p for p in candidate_paths if os.path.exists(p)),
                    candidate_paths[-1] if candidate_paths else None,
                )
                if skill_md_path is None:
                    # 个人技能但解析不出个人目录（无身份 / 工作区根解析异常）且没有显式
                    # 路径时，候选链是空的：此前 `candidate_paths[-1]` 直接 IndexError，
                    # 而这个异常会把本轮**所有**技能（含公共与自动匹配）一起带走。
                    # 只跳过这一条，剩下的技能照常注入。
                    logger.warning(
                        "[Skills] Skip mounted skill %s: personal skills dir unresolved "
                        "(scope=%s, has_user_info=%s)",
                        skill_id,
                        skill_scope,
                        bool(user_info),
                    )
                    continue
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
                        scope=read_scope,
                        skill_md_path=skill_md_path if os.path.exists(skill_md_path) else None,
                        include_resource_tree=True,
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
                activated_skill_ids.append(str(skill_id))
                logger.info(
                    "[Skills] Matched mounted skill %s (%s).",
                    skill_id,
                    "full instruction preloaded" if full_instruction else "summary only",
                )
                # 会话固定技能（不在单条消息 files 中）或因上限降级为仅摘要的挂载技能，
                # 推送时间线日志以便前端徽标与执行轨迹如实呈现状态。
                if skills_log_callback and (
                    skill_obj.get("_from_resource_scope")
                    or (str(skill_id) not in explicit_message_skill_ids)
                    or not full_instruction
                ):
                    if full_instruction:
                        details_msg = (
                            f"已加载会话固定技能「{skill_name}」(ID: {skill_id})。"
                            "已预载完整 SKILL.md 指令，本轮可直接按该技能执行。"
                        )
                    else:
                        details_msg = (
                            f"已加载挂载技能「{skill_name}」(ID: {skill_id})摘要。"
                            "受单轮预载数量上限约束，正文未预载；执行前将通过 read_skill_instruction 读取全文。"
                        )
                    skills_log_callback(skill_id, skill_name, details_msg)

        # 仅当「有效的会话固定技能范围」存在时才跳过口头点名：
        # 1) 显式挂载了 A、同时在提问里口头点名 B → 两者都应注入（不因挂载 A 而丢掉 B）；
        # 2) 会话固定技能若全部被禁用/白名单过滤，允许正常回落到口头点名。
        has_active_scoped_skills = any(
            isinstance(s, dict) and s.get("_from_resource_scope") for s in active_skills
        )
        if clean_user_query and not has_active_scoped_skills:
            try:
                from app.services.ai.skill_resolver import (
                    load_skill_md_content,
                    resolve_skills_from_query,
                    skill_filter_kwargs_from_config,
                )

                skill_filter = skill_filter_kwargs_from_config(agent_config)
                skill_filter["excluded_skill_ids"] = forbidden_skill_ids
                for skill_meta in resolve_skills_from_query(
                    clean_user_query,
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
                            include_resource_tree=True,
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
                    activated_skill_ids.append(str(skill_id))
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
                                "已预载完整 SKILL.md 指令，本轮可直接按该技能执行。"
                            )
                        skills_log_callback(skill_id, skill_name, details_msg)
            except Exception as resolve_err:
                logger.warning("[Skills] Failed to auto-resolve skills from query: %s", resolve_err)

        if clean_user_query and not skills_injection:
            try:
                from app.services.ai.skill_resolver import (
                    is_main_general_agent,
                    load_skill_md_content,
                    scan_relevant_skills,
                    should_scan_skills_for_query,
                    skill_filter_kwargs_from_config,
                )

                skill_filter = skill_filter_kwargs_from_config(agent_config)
                # 主助手扫描全部可见技能；配置了自定义技能白名单（skills_custom=True）的专家
                # 智能体同样在白名单小范围内开启自动扫描，提升专属技能的自动触发率。
                if is_main_general_agent(agent_config) or bool(skill_filter.get("skills_custom")):
                    skill_filter["excluded_skill_ids"] = forbidden_skill_ids
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
                        if should_scan_skills_for_query(clean_user_query):
                            scanned_skills = scan_relevant_skills(
                                clean_user_query,
                                user_info=user_info,
                                exclude_ids=mounted_skill_ids,
                                max_results=max_scan_results,
                                min_score=min_score,
                                **skill_filter,
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
                            if cls.should_preload_skill_full_instruction(
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
                                    include_resource_tree=True,
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
                            if full_instruction:
                                activated_skill_ids.append(str(skill_id))
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
                                    details_msg = (
                                        f"已根据本轮问题扫描技能库并匹配「{skill_name}」(ID: {skill_id}){score_hint}。"
                                        "已预载完整 SKILL.md 指令，本轮可直接按该技能执行。"
                                    )
                                else:
                                    details_msg = (
                                        f"已根据本轮问题扫描技能库并匹配「{skill_name}」(ID: {skill_id}){score_hint}。"
                                        f"已注入摘要；模型须调用 read_skill_instruction 读取 SKILL.md 全文后再执行。"
                                    )
                                skills_log_callback(skill_id, skill_name, details_msg)
            except Exception as scan_err:
                logger.warning("[Skills] Failed to scan skills from query: %s", scan_err)

        # 紧邻追问轮轻量延续：上一轮刚激活过某技能，本轮用户继续追问（如「按刚才流程再跑一下」）
        # 且未命中新技能时，以摘要态轻量附带上一轮技能，既省 token 又避免流程断层。
        if clean_user_query and not skills_injection and conversation_id:
            try:
                recent_ids = get_recent_session_skills(user_info, conversation_id)
                if recent_ids and _looks_like_skill_followup_query(clean_user_query):
                    from app.services.ai.skill_resolver import (
                        list_skill_metas,
                        skill_filter_kwargs_from_config,
                    )
                    from app.utils.skill_metadata import is_skill_enabled

                    skill_filter = skill_filter_kwargs_from_config(agent_config)
                    visible_metas = {
                        str(meta.get("id")): meta
                        for meta in list_skill_metas(
                            user_info=user_info,
                            excluded_skill_ids=forbidden_skill_ids,
                            **skill_filter,
                        )
                        if meta.get("id") and is_skill_enabled(meta)
                    }
                    for skill_id in recent_ids[:2]:
                        meta = visible_metas.get(skill_id)
                        if not meta or skill_id in mounted_skill_ids:
                            continue
                        skill_name = meta.get("name") or skill_id
                        description = meta.get("description") or ""
                        skills_injection.append(
                            cls.build_skill_injection(
                                skill_name=skill_name,
                                skill_id=skill_id,
                                description=description,
                                full_instruction=None,
                            )
                        )
                        mounted_skill_ids.add(skill_id)
                        logger.info(
                            "[Skills] Carried over recent session skill %s (summary only) for follow-up.",
                            skill_id,
                        )
                        if skills_log_callback:
                            skills_log_callback(
                                skill_id,
                                skill_name,
                                (
                                    f"检测到紧邻追问，延续上一轮技能「{skill_name}」(ID: {skill_id})摘要；"
                                    "若本轮需继续按该技能执行，模型将调用 read_skill_instruction 读取全文。"
                                ),
                            )
            except Exception as followup_err:
                logger.debug("[Skills] Recent skill follow-up carry-over skipped: %s", followup_err)

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

        # 记录本轮真正激活的技能（供紧邻追问延续 & 使用量统计）
        if activated_skill_ids:
            if conversation_id:
                remember_recent_session_skills(user_info, conversation_id, activated_skill_ids)
            try:
                from app.services.ai.skills_stats_service import skills_stats_service
                await skills_stats_service.record_activations(set(activated_skill_ids))
            except Exception as stats_err:
                logger.error(f"[SkillsStats] Auto-recording skill activations failed: {stats_err}")

        return skills_injection
