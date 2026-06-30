"""基于 ChatBI {dataset_menu} 与用户权限，由 LLM 生成我的数据门户（含 quick 追问按钮）。"""
from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts, is_dataset_portal_slash_query
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)

_NAV_CACHE_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days
_CLICK_STATS_TTL_SECONDS = 90 * 24 * 60 * 60
_RECENT_REFRESH_QUESTIONS_TTL_SECONDS = 5 * 60
_RECENT_REFRESH_QUESTIONS_LIMIT = 80
_LEGACY_NAV_CACHE_GEN_KEY = "agent:dataset_navigation:cache_generation"


def _user_cache_key(*, user_id: Optional[int], is_admin: bool) -> str:
    if is_admin:
        return "admin"
    if user_id is not None:
        return str(user_id)
    return "anon"


def count_datasets_in_menu(dataset_menu: str) -> int:
    return len(re.findall(r"^- Dataset:", str(dataset_menu or ""), flags=re.MULTILINE))


def menu_has_authorized_datasets(dataset_menu: str) -> bool:
    text = str(dataset_menu or "")
    if not text.strip():
        return False
    if "No authorized datasets" in text:
        return False
    return count_datasets_in_menu(text) > 0


def _question_hash(query: str) -> str:
    return hashlib.sha1(str(query or "").strip().encode("utf-8")).hexdigest()[:16]


def _decode_redis_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _normalize_question_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower(), flags=re.UNICODE)


def _question_texts_similar(left: str, right: str) -> bool:
    left_norm = _normalize_question_text(left)
    right_norm = _normalize_question_text(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if len(shorter) >= 8 and shorter in longer:
        return True
    if SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.86:
        return True
    if len(left_norm) < 2 or len(right_norm) < 2:
        return False
    left_pairs = {left_norm[i : i + 2] for i in range(len(left_norm) - 1)}
    right_pairs = {right_norm[i : i + 2] for i in range(len(right_norm) - 1)}
    if not left_pairs or not right_pairs:
        return False
    return len(left_pairs & right_pairs) / len(left_pairs | right_pairs) >= 0.72


class DatasetNavigationService:
    @staticmethod
    async def _get_dataset_menu(
        *,
        user_id: Optional[int],
        is_admin: bool,
        force_refresh: bool = False,
    ) -> str:
        return await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin, force_refresh=force_refresh)

    @staticmethod
    def _navigation_cache_key(user_key: str) -> str:
        return f"agent:dataset_navigation:{user_key}"

    @staticmethod
    def _click_rank_key(user_key: str) -> str:
        return f"agent:dataset_navigation_click_rank:{user_key}"

    @staticmethod
    def _click_meta_key(user_key: str) -> str:
        return f"agent:dataset_navigation_click_meta:{user_key}"

    @staticmethod
    def _recent_refresh_questions_key(
        *,
        user_key: str,
        purpose: str,
        group_identity: str,
    ) -> str:
        clean_purpose = re.sub(r"[^0-9A-Za-z_-]+", "_", str(purpose or "questions").strip()) or "questions"
        group_hash = _question_hash(group_identity or "unknown")
        return f"agent:dataset_navigation_recent_questions:{user_key}:{clean_purpose}:{group_hash}"

    @staticmethod
    async def invalidate_navigation_cache_for_user(
        *,
        user_id: Optional[int] = None,
        is_admin: bool = False,
    ) -> None:
        """权限或目录变更后，清理指定用户的数据门户 Redis 缓存。"""
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        try:
            redis_client = await get_redis()
            if not redis_client:
                return
            await redis_client.delete(
                DatasetNavigationService._navigation_cache_key(user_key),
                DatasetNavigationService._click_rank_key(user_key),
                DatasetNavigationService._click_meta_key(user_key),
            )
            pattern = f"agent:dataset_navigation_recent_questions:{user_key}:*"
            async for key in redis_client.scan_iter(match=pattern, count=200):
                await redis_client.delete(key)
        except Exception as e:
            logger.warning("Dataset navigation cache invalidate failed for %s: %s", user_key, e)

    @staticmethod
    async def invalidate_all_navigation_caches() -> None:
        """元数据变更后清理全部用户的数据门户 Redis 缓存（含历史 menu_hash 旧 Key）。"""
        patterns = (
            "agent:dataset_navigation:*",
            "agent:dataset_navigation_click_rank:*",
            "agent:dataset_navigation_click_meta:*",
            "agent:dataset_navigation_recent_questions:*",
        )
        try:
            redis_client = await get_redis()
            if not redis_client:
                return
            for pattern in patterns:
                async for key in redis_client.scan_iter(match=pattern, count=200):
                    await redis_client.delete(key)
            await redis_client.delete(_LEGACY_NAV_CACHE_GEN_KEY)
        except Exception as e:
            logger.warning("Dataset navigation global cache invalidate failed: %s", e)

    @staticmethod
    async def bump_navigation_cache_generation() -> None:
        """兼容旧调用：改为直接删除门户相关 Redis 缓存。"""
        await DatasetNavigationService.invalidate_all_navigation_caches()

    @staticmethod
    async def _load_cached_navigation(cache_key: str) -> Optional[str]:
        try:
            redis = await get_redis()
            if redis:
                cached = await redis.get(cache_key)
                if cached:
                    return str(cached)
        except Exception as e:
            logger.warning("Dataset navigation cache read failed: %s", e)
        return None

    @staticmethod
    async def _save_cached_navigation(
        cache_key: str,
        markdown: str,
        ttl: int = _NAV_CACHE_TTL_SECONDS,
    ) -> None:
        try:
            redis = await get_redis()
            if redis:
                await redis.set(cache_key, markdown, ex=ttl)
        except Exception as e:
            logger.warning("Dataset navigation cache write failed: %s", e)

    @staticmethod
    async def _generate_navigation_markdown(dataset_menu: str) -> tuple[str, str | None]:
        if not menu_has_authorized_datasets(dataset_menu):
            return (
                finalize_visible_reply(
                    DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu),
                    collapse_duplicates=False,
                ),
                None,
            )

        fallback = DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)
        fallback_md = finalize_visible_reply(fallback, collapse_duplicates=False)
        try:
            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            chat_client = chat_client_from_handle(llm)
            content = await chat_client.generate_text(
                system_user_prompt_messages(
                    DataQueryPrompts.dataset_navigation_generation_prompt(dataset_menu),
                    user_prompt="请生成完整的数据门户 Markdown。",
                )
            )
            cleaned = str(content or "").strip()
            if cleaned and DataQueryPrompts.has_quick_suggestions(cleaned):
                return finalize_visible_reply(cleaned, collapse_duplicates=False), None
            return fallback_md, "模型返回内容无效或未包含推荐问题"
        except Exception as e:
            logger.warning("Dataset navigation LLM generation failed: %s", e)
            err = str(e).strip() or "模型调用失败"
            return fallback_md, err[:240]

    @staticmethod
    def _extract_question_queries(questions: Optional[list[Any]]) -> list[str]:
        extracted: list[str] = []
        for item in questions or []:
            if isinstance(item, dict):
                text = str(item.get("query") or item.get("label") or "").strip()
            else:
                text = str(item or "").strip()
            if text and text not in extracted:
                extracted.append(text)
        return extracted

    @staticmethod
    async def _load_recent_refresh_questions(
        *,
        user_key: str,
        purpose: str,
        group_identity: str,
    ) -> list[str]:
        try:
            redis = await get_redis()
            if not redis:
                return []
            key = DatasetNavigationService._recent_refresh_questions_key(
                user_key=user_key,
                purpose=purpose,
                group_identity=group_identity,
            )
            raw = await redis.get(key)
            raw = _decode_redis_value(raw)
            if not raw:
                return []
            values = json.loads(str(raw))
            if not isinstance(values, list):
                return []
            return [str(v).strip() for v in values if str(v or "").strip()]
        except Exception as e:
            logger.warning("Dataset navigation recent questions read failed: %s", e)
            return []

    @staticmethod
    async def _remember_recent_refresh_questions(
        *,
        user_key: str,
        purpose: str,
        group_identity: str,
        questions: list[dict[str, Any]],
    ) -> None:
        new_queries = DatasetNavigationService._extract_question_queries(questions)
        if not new_queries:
            return
        try:
            redis = await get_redis()
            if not redis:
                return
            key = DatasetNavigationService._recent_refresh_questions_key(
                user_key=user_key,
                purpose=purpose,
                group_identity=group_identity,
            )
            raw = _decode_redis_value(await redis.get(key))
            try:
                existing_payload = json.loads(str(raw)) if raw else []
            except Exception:
                existing_payload = []
            existing = []
            if isinstance(existing_payload, list):
                existing = [str(v).strip() for v in existing_payload if str(v or "").strip()]
            merged: list[str] = []
            for query in [*new_queries, *existing]:
                if query and query not in merged:
                    merged.append(query)
            await redis.set(
                key,
                json.dumps(merged[:_RECENT_REFRESH_QUESTIONS_LIMIT], ensure_ascii=False),
                ex=_RECENT_REFRESH_QUESTIONS_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("Dataset navigation recent questions write failed: %s", e)

    @staticmethod
    async def _filter_authorized_tables(
        *,
        tables: list[str],
        user_id: Optional[int],
        is_admin: bool,
    ) -> list[str]:
        requested: list[str] = []
        for table in tables or []:
            term = str(table or "").strip()
            if term and term not in requested:
                requested.append(term)
        if not requested:
            return []
        if user_id is None and not is_admin:
            return requested
        dataset_menu = await DatasetNavigationService._get_dataset_menu(user_id=user_id, is_admin=is_admin)
        allowed: set[str] = set()
        for block in DataQueryPrompts._parse_dataset_blocks(dataset_menu):
            for table in block.get("tables") or []:
                term = str(table.get("term") or "").strip()
                if term:
                    allowed.add(term)
        if not allowed:
            return []
        return [table for table in requested if table in allowed]

    @staticmethod
    def _filter_new_questions(
        questions: list[dict[str, Any]],
        excluded_queries: list[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        comparison_pool = list(excluded_queries)
        for question in questions or []:
            query = str(question.get("query") or "").strip()
            if not query:
                continue
            if any(_question_texts_similar(query, existing) for existing in comparison_pool):
                continue
            accepted.append(question)
            comparison_pool.append(query)
            if len(accepted) >= limit:
                break
        return accepted

    @staticmethod
    async def _load_question_click_stats(
        *,
        user_key: str,
    ) -> Dict[str, Dict[str, Any]]:
        try:
            redis = await get_redis()
            if not redis:
                return {}
            rank_key = DatasetNavigationService._click_rank_key(user_key)
            meta_key = DatasetNavigationService._click_meta_key(user_key)
            ranked = await redis.zrevrange(rank_key, 0, -1, withscores=True)
            raw_meta = await redis.hgetall(meta_key)
            meta_map: Dict[str, Any] = {}
            if isinstance(raw_meta, dict):
                meta_map = {
                    str(_decode_redis_value(k)): _decode_redis_value(v)
                    for k, v in raw_meta.items()
                }

            stats: Dict[str, Dict[str, Any]] = {}
            for item in ranked or []:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    member, score = item[0], item[1]
                else:
                    member, score = item, 0
                question_id = str(_decode_redis_value(member))
                meta_raw = meta_map.get(question_id)
                if not meta_raw:
                    continue
                try:
                    meta = json.loads(str(meta_raw))
                except Exception:
                    continue
                query = str(meta.get("query") or "").strip()
                if not query:
                    continue
                stats[query] = {
                    "count": int(score or 0),
                    "last_clicked_at": meta.get("last_clicked_at"),
                    "label": meta.get("label"),
                    "group_id": meta.get("group_id"),
                }
            return stats
        except Exception as e:
            logger.warning("Dataset navigation click stats read failed: %s", e)
            return {}

    @staticmethod
    def _apply_question_click_stats(
        groups: list[dict[str, Any]],
        click_stats: Dict[str, Dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not click_stats:
            return groups

        ranked_groups: list[dict[str, Any]] = []
        for group in groups:
            copied = dict(group)
            questions = []
            for index, question in enumerate(group.get("questions") or []):
                query = str(question.get("query") or "")
                stats = click_stats.get(query) or {}
                enriched = dict(question)
                enriched["click_count"] = int(stats.get("count") or 0)
                if stats.get("last_clicked_at"):
                    enriched["last_clicked_at"] = stats["last_clicked_at"]
                enriched["_original_order"] = index
                questions.append(enriched)

            questions.sort(
                key=lambda q: (
                    int(q.get("click_count") or 0),
                    str(q.get("last_clicked_at") or ""),
                    -int(q.get("_original_order") or 0),
                ),
                reverse=True,
            )
            for question in questions:
                question.pop("_original_order", None)
            copied["questions"] = questions
            ranked_groups.append(copied)
        return ranked_groups

    @staticmethod
    async def record_question_click(
        *,
        user_id: Optional[int],
        is_admin: bool,
        query: str,
        label: Optional[str] = None,
        group_id: Optional[str] = None,
        dataset_menu_hash: Optional[str] = None,
    ) -> None:
        del dataset_menu_hash
        clean_query = str(query or "").strip()
        if not clean_query:
            return
        try:
            redis = await get_redis()
            if not redis:
                return
            user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
            question_id = _question_hash(clean_query)
            rank_key = DatasetNavigationService._click_rank_key(user_key)
            meta_key = DatasetNavigationService._click_meta_key(user_key)
            now = datetime.now(timezone.utc).isoformat()
            metadata = {
                "query": clean_query,
                "label": str(label or "").strip(),
                "group_id": str(group_id or "").strip(),
                "last_clicked_at": now,
            }
            await redis.zincrby(rank_key, 1, question_id)
            await redis.hset(meta_key, question_id, json.dumps(metadata, ensure_ascii=False))
            await redis.expire(rank_key, _CLICK_STATS_TTL_SECONDS)
            await redis.expire(meta_key, _CLICK_STATS_TTL_SECONDS)
        except Exception as e:
            logger.warning("Dataset navigation click stats write failed: %s", e)

    @staticmethod
    async def clear_question_click(
        *,
        user_id: Optional[int],
        is_admin: bool,
        query: str,
        dataset_menu_hash: Optional[str] = None,
    ) -> bool:
        del dataset_menu_hash
        clean_query = str(query or "").strip()
        if not clean_query:
            return False
        try:
            redis = await get_redis()
            if not redis:
                return False
            user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
            question_id = _question_hash(clean_query)
            rank_key = DatasetNavigationService._click_rank_key(user_key)
            meta_key = DatasetNavigationService._click_meta_key(user_key)
            removed_rank = int(await redis.zrem(rank_key, question_id) or 0)
            removed_meta = int(await redis.hdel(meta_key, question_id) or 0)
            return removed_rank > 0 or removed_meta > 0
        except Exception as e:
            logger.warning("Dataset navigation click stats clear failed: %s", e)
            return False

    @staticmethod
    def parse_groups_from_markdown(
        markdown: str,
        static_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """从 LLM 生成的 markdown 文本中，动态解析出各个场景下的推荐问题和继续追问，
        并与静态分析的 static_groups 进行合并。
        """
        if not markdown or not static_groups:
            return static_groups

        import copy
        groups = copy.deepcopy(static_groups)

        parts = markdown.split("#### ")
        if len(parts) <= 1:
            return groups

        def clean_str(s: str) -> str:
            return re.sub(r"[^\w]", "", s).lower()

        pattern = r"-\s*\[(?:[^\]]*🙋\s*)?([^\]]+)\]\(quick:([^\)]+)\)"

        for part in parts[1:]:
            lines = part.splitlines()
            if not lines:
                continue
            title = lines[0].strip()
            if not title:
                continue

            matched_group = None
            clean_title = clean_str(title)
            for g in groups:
                if clean_str(g.get("title") or "") == clean_title:
                    matched_group = g
                    break
            if not matched_group:
                for g in groups:
                    clean_g_title = clean_str(g.get("title") or "")
                    if clean_title == clean_g_title or clean_title in clean_g_title or clean_g_title in clean_title:
                        matched_group = g
                        break

            if not matched_group:
                continue

            lower_part = part.lower()
            split_term = "**继续追问：**".lower()
            split_idx = lower_part.find(split_term)

            if split_idx != -1:
                questions_part = part[:split_idx]
                followups_part = part[split_idx:]
            else:
                questions_part = part
                followups_part = ""

            dynamic_questions = []
            for match in re.finditer(pattern, questions_part):
                label = match.group(1).strip()
                query = match.group(2).strip()
                if is_dataset_portal_slash_query(query) or "重新查看" in label:
                    continue
                dynamic_questions.append({
                    "label": label,
                    "query": query,
                    "type": "dynamic",
                })

            dynamic_followups = []
            for match in re.finditer(pattern, followups_part):
                label = match.group(1).strip()
                query = match.group(2).strip()
                if is_dataset_portal_slash_query(query) or "重新查看" in label:
                    continue
                dynamic_followups.append({
                    "label": label,
                    "query": query,
                })

            summary_lines = []
            for line in lines[1:]:
                stripped = line.strip()
                if stripped.startswith(">"):
                    summary_lines.append(stripped.lstrip(">").strip())

            if dynamic_questions:
                matched_group["questions"] = dynamic_questions
            if dynamic_followups:
                matched_group["followups"] = dynamic_followups
            if summary_lines:
                matched_group["summary"] = "\n".join(summary_lines)

        return groups

    @staticmethod
    async def _fetch_columns_for_groups(
        db: AsyncSession,
        groups: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """为所有 groups 中的表术语 (table term) 批量拉取字段数据字典定义"""
        table_terms = []
        for g in groups:
            for r in g.get("related_data") or []:
                for t in r.get("tables") or []:
                    if t:
                        table_terms.append(str(t).strip())

        if not table_terms:
            return {}

        from sqlalchemy import select
        from app.models.metadata import MetaTable, MetaColumn

        try:
            stmt = (
                select(
                    MetaTable.term.label("table_term"),
                    MetaColumn.physical_name,
                    MetaColumn.term.label("column_term"),
                    MetaColumn.type,
                    MetaColumn.description,
                )
                .join(MetaColumn, MetaTable.id == MetaColumn.table_id)
                .where(MetaTable.term.in_(table_terms))
                .where(MetaTable.status == 1)
                .order_by(MetaColumn.id.asc())
            )
            res = await db.execute(stmt)
            rows = res.all()

            table_to_columns = {}
            for row in rows:
                t_term = str(row.table_term).strip()
                if t_term not in table_to_columns:
                    table_to_columns[t_term] = []
                table_to_columns[t_term].append({
                    "name": row.physical_name,
                    "term": row.column_term,
                    "type": row.type or "",
                    "description": row.description or "",
                })
            return table_to_columns
        except Exception as e:
            logger.warning("Failed to fetch metadata columns for navigation: %s", e)
            return {}

    @staticmethod
    async def build_navigation_for_user(
        db: AsyncSession,
        *,
        user_id: Optional[int],
        is_admin: bool,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        dataset_menu = await DatasetNavigationService._get_dataset_menu(
            user_id=user_id,
            is_admin=is_admin,
            force_refresh=force_refresh,
        )
        dataset_count = count_datasets_in_menu(dataset_menu)

        menu_hash = hashlib.md5(dataset_menu.encode("utf-8")).hexdigest()[:12]
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        cache_key = DatasetNavigationService._navigation_cache_key(user_key)
        groups = DataQueryPrompts.build_dataset_navigation_groups(dataset_menu)
        has_datasets = menu_has_authorized_datasets(dataset_menu)

        if not has_datasets:
            markdown = finalize_visible_reply(
                DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu),
                collapse_duplicates=False,
            )
            return {
                "dataset_count": 0,
                "dataset_menu_hash": menu_hash,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "groups": [],
                "markdown": markdown,
                "is_fallback": False,
                "has_datasets": False,
                "from_cache": False,
                "llm_generation_failed": False,
                "llm_error_message": None,
            }

        from_cache = False
        llm_generation_failed = False
        llm_error_message: str | None = None
        markdown = None if force_refresh else await DatasetNavigationService._load_cached_navigation(cache_key)
        if markdown:
            from_cache = True
        if not markdown:
            markdown, llm_err = await DatasetNavigationService._generate_navigation_markdown(dataset_menu)
            if llm_err:
                llm_generation_failed = True
                llm_error_message = llm_err
            
            # 检测是否因大模型报错或生成失败而降级到了兜底模板
            fallback_raw = DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)
            fallback_md = finalize_visible_reply(fallback_raw, collapse_duplicates=False)
            is_fallback = (markdown == fallback_md)
            
            # 如果是异常兜底（有授权数据集但生成了兜底文本），只缓存 15 秒，避免长期卡在兜底状态；正常 LLM 结果缓存 90 天
            ttl = 15 if (is_fallback and has_datasets) else _NAV_CACHE_TTL_SECONDS
            await DatasetNavigationService._save_cached_navigation(cache_key, markdown, ttl=ttl)

        # 始终检测当前是否为降级兜底状态
        fallback_raw = DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)
        fallback_md = finalize_visible_reply(fallback_raw, collapse_duplicates=False)
        is_fallback = (markdown == fallback_md)

        # 动态解析 markdown 中的场景和推荐问题
        groups = DatasetNavigationService.parse_groups_from_markdown(markdown, groups)

        # 批量拉取数据表下的列名字典释义
        table_to_columns = await DatasetNavigationService._fetch_columns_for_groups(db, groups)

        # 批量拉取物理表名映射
        table_physical_names = {}
        try:
            from sqlalchemy import select
            from app.models.metadata import MetaTable
            all_table_terms = []
            for g in groups:
                for r in g.get("related_data") or []:
                    for t in r.get("tables") or []:
                        if t:
                            all_table_terms.append(str(t).strip())
            if all_table_terms:
                t_stmt = select(MetaTable.term, MetaTable.physical_name).where(
                    MetaTable.term.in_(all_table_terms),
                    MetaTable.status == 1
                )
                t_res = await db.execute(t_stmt)
                for t_row in t_res.all():
                    table_physical_names[str(t_row.term).strip()] = str(t_row.physical_name).strip()
        except Exception as e:
            logger.warning("Failed to fetch table physical names: %s", e)

        for g in groups:
            for r in g.get("related_data") or []:
                r["table_columns"] = {
                    t: table_to_columns.get(t, [])
                    for t in r.get("tables") or []
                }
                r["table_physical_names"] = {
                    t: table_physical_names.get(t, "")
                    for t in r.get("tables") or []
                }

        # 应用用户点击的偏好排序
        click_stats = await DatasetNavigationService._load_question_click_stats(
            user_key=user_key,
        )
        groups = DatasetNavigationService._apply_question_click_stats(groups, click_stats)

        # 记录原始索引并计算场景卡片（Group）的问题点击总数进行稳定重排
        for idx, g in enumerate(groups):
            g["_original_index"] = idx
            g["total_click_count"] = sum(int(q.get("click_count") or 0) for q in g.get("questions") or [])

        groups.sort(
            key=lambda g: (
                g.get("total_click_count") or 0,
                -g.get("_original_index", 0)
            ),
            reverse=True
        )

        for g in groups:
            g.pop("_original_index", None)

        return {
            "dataset_count": dataset_count,
            "dataset_menu_hash": menu_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "groups": groups,
            "markdown": markdown,
            "is_fallback": is_fallback,
            "has_datasets": has_datasets,
            "from_cache": from_cache,
            "llm_generation_failed": llm_generation_failed,
            "llm_error_message": llm_error_message,
        }

    @staticmethod
    async def refresh_group_questions(
        db: AsyncSession,
        *,
        group_title: str,
        tables: list[str],
        user_id: Optional[int] = None,
        is_admin: bool = False,
        group_id: str = "",
        exclude_questions: Optional[list[Any]] = None,
    ) -> list[dict[str, Any]]:
        """针对特定的卡片场景 (group_title) 和关联表列表 (tables)，在线实时调用大模型生成 3 个新问题"""
        purpose = "questions"
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        group_identity = str(group_id or group_title or "").strip()
        authorized_tables = await DatasetNavigationService._filter_authorized_tables(
            tables=tables,
            user_id=user_id,
            is_admin=is_admin,
        )
        if not authorized_tables:
            return []

        recent_questions = await DatasetNavigationService._load_recent_refresh_questions(
            user_key=user_key,
            purpose=purpose,
            group_identity=group_identity,
        )
        excluded_queries = [
            *DatasetNavigationService._extract_question_queries(exclude_questions),
            *recent_questions,
        ]
        table_physical_names, table_to_columns = await DatasetNavigationService._load_table_metadata(
            db,
            authorized_tables,
        )

        prompt = DataQueryPrompts.build_group_questions_refresh_prompt(
            group_title=group_title,
            tables=authorized_tables,
            table_to_columns=table_to_columns,
            table_physical_names=table_physical_names,
            exclude_questions=excluded_queries,
        )
        generated = await DatasetNavigationService._generate_questions_via_llm(prompt)
        questions = DatasetNavigationService._filter_new_questions(generated, excluded_queries, limit=3)
        if len(questions) < 3:
            retry_exclusions = [
                *excluded_queries,
                *DatasetNavigationService._extract_question_queries(generated),
            ]
            retry_prompt = DataQueryPrompts.build_group_questions_refresh_prompt(
                group_title=group_title,
                tables=authorized_tables,
                table_to_columns=table_to_columns,
                table_physical_names=table_physical_names,
                exclude_questions=retry_exclusions,
            )
            retry_generated = await DatasetNavigationService._generate_questions_via_llm(retry_prompt)
            questions = [
                *questions,
                *DatasetNavigationService._filter_new_questions(
                    retry_generated,
                    [
                        *excluded_queries,
                        *DatasetNavigationService._extract_question_queries(questions),
                    ],
                    limit=3 - len(questions),
                ),
            ]
        await DatasetNavigationService._remember_recent_refresh_questions(
            user_key=user_key,
            purpose=purpose,
            group_identity=group_identity,
            questions=questions,
        )
        return questions

    @staticmethod
    async def refresh_group_followups(
        db: AsyncSession,
        *,
        group_title: str,
        tables: list[str],
        user_id: Optional[int] = None,
        is_admin: bool = False,
        group_id: str = "",
        exclude_questions: Optional[list[Any]] = None,
    ) -> list[dict[str, Any]]:
        """针对场景卡片生成 2 条「继续追问」探索性问题。"""
        purpose = "followups"
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        group_identity = str(group_id or group_title or "").strip()
        authorized_tables = await DatasetNavigationService._filter_authorized_tables(
            tables=tables,
            user_id=user_id,
            is_admin=is_admin,
        )
        if not authorized_tables:
            return []
        recent_questions = await DatasetNavigationService._load_recent_refresh_questions(
            user_key=user_key,
            purpose=purpose,
            group_identity=group_identity,
        )
        excluded_queries = [
            *DatasetNavigationService._extract_question_queries(exclude_questions),
            *recent_questions,
        ]
        table_physical_names, table_to_columns = await DatasetNavigationService._load_table_metadata(
            db,
            authorized_tables,
        )
        prompt = DataQueryPrompts.build_group_followups_refresh_prompt(
            group_title=group_title,
            tables=authorized_tables,
            table_to_columns=table_to_columns,
            table_physical_names=table_physical_names,
            exclude_questions=excluded_queries,
        )
        generated = await DatasetNavigationService._generate_questions_via_llm(prompt)
        questions = DatasetNavigationService._filter_new_questions(generated, excluded_queries, limit=2)
        if len(questions) < 2:
            retry_exclusions = [
                *excluded_queries,
                *DatasetNavigationService._extract_question_queries(generated),
            ]
            retry_prompt = DataQueryPrompts.build_group_followups_refresh_prompt(
                group_title=group_title,
                tables=authorized_tables,
                table_to_columns=table_to_columns,
                table_physical_names=table_physical_names,
                exclude_questions=retry_exclusions,
            )
            retry_generated = await DatasetNavigationService._generate_questions_via_llm(retry_prompt)
            questions = [
                *questions,
                *DatasetNavigationService._filter_new_questions(
                    retry_generated,
                    [
                        *excluded_queries,
                        *DatasetNavigationService._extract_question_queries(questions),
                    ],
                    limit=2 - len(questions),
                ),
            ]
        await DatasetNavigationService._remember_recent_refresh_questions(
            user_key=user_key,
            purpose=purpose,
            group_identity=group_identity,
            questions=questions,
        )
        return questions

    @staticmethod
    def _parse_quick_questions_from_llm(content: str) -> list[dict[str, Any]]:
        cleaned = str(content or "").strip()
        questions: list[dict[str, Any]] = []
        pattern = r"-\s*\[(?:[^\]]*🙋\s*)?([^\]]+)\]\(quick:([^\)]+)\)"
        for match in re.finditer(pattern, cleaned):
            label = match.group(1).strip()
            query = match.group(2).strip()
            if is_dataset_portal_slash_query(query) or "重新查看" in label:
                continue
            questions.append({
                "label": label,
                "query": query,
                "type": "dynamic",
            })
        return questions

    @staticmethod
    async def _generate_questions_via_llm(prompt: str) -> list[dict[str, Any]]:
        try:
            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            chat_client = chat_client_from_handle(llm)
            content = await chat_client.generate_text(
                system_user_prompt_messages(
                    prompt,
                    user_prompt="请生成推荐问题。",
                )
            )
            return DatasetNavigationService._parse_quick_questions_from_llm(content)
        except Exception as e:
            logger.warning("Failed to generate dataset menu questions via LLM: %s", e)
            return []

    @staticmethod
    async def _load_table_metadata(
        db: AsyncSession,
        tables: list[str],
    ) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
        table_physical_names: dict[str, str] = {}
        table_to_columns: dict[str, list[dict[str, Any]]] = {}
        if not tables:
            return table_physical_names, table_to_columns

        try:
            from sqlalchemy import select
            from app.models.metadata import MetaTable, MetaColumn

            t_stmt = select(MetaTable.term, MetaTable.physical_name).where(
                MetaTable.term.in_(tables),
                MetaTable.status == 1,
            )
            t_res = await db.execute(t_stmt)
            for t_row in t_res.all():
                table_physical_names[str(t_row.term).strip()] = str(t_row.physical_name).strip()

            stmt = (
                select(
                    MetaTable.term.label("table_term"),
                    MetaColumn.physical_name,
                    MetaColumn.term.label("column_term"),
                    MetaColumn.type,
                    MetaColumn.description,
                )
                .join(MetaColumn, MetaTable.id == MetaColumn.table_id)
                .where(MetaTable.term.in_(tables))
                .where(MetaTable.status == 1)
                .order_by(MetaColumn.id.asc())
            )
            res = await db.execute(stmt)
            for row in res.all():
                t_term = str(row.table_term).strip()
                if t_term not in table_to_columns:
                    table_to_columns[t_term] = []
                table_to_columns[t_term].append({
                    "name": row.physical_name,
                    "term": row.column_term,
                    "type": row.type or "",
                    "description": row.description or "",
                })
        except Exception as e:
            logger.warning("Failed to fetch metadata columns or table physical names: %s", e)

        return table_physical_names, table_to_columns

    @staticmethod
    async def recommend_table_questions(
        db: AsyncSession,
        *,
        table: str,
        physical_table_name: str = "",
        dataset_name: str = "",
        columns: Optional[list[dict[str, Any]]] = None,
    ) -> list[dict[str, Any]]:
        """基于单表字段定义在线生成推荐提问，不触发 ChatBI 查数链路。"""
        table_term = str(table or "").strip()
        if not table_term:
            return []

        normalized_columns = [
            {
                "name": str(col.get("name") or "").strip(),
                "term": str(col.get("term") or "").strip(),
                "type": str(col.get("type") or "").strip(),
                "description": str(col.get("description") or "").strip(),
            }
            for col in (columns or [])
            if str(col.get("name") or col.get("term") or "").strip()
        ]

        physical_name = str(physical_table_name or "").strip()
        if not normalized_columns or not physical_name:
            table_physical_names, table_to_columns = await DatasetNavigationService._load_table_metadata(
                db,
                [table_term],
            )
            if not physical_name:
                physical_name = table_physical_names.get(table_term, "")
            if not normalized_columns:
                normalized_columns = table_to_columns.get(table_term, [])

        prompt = DataQueryPrompts.build_table_questions_recommend_prompt(
            table=table_term,
            columns=normalized_columns,
            physical_table_name=physical_name,
            dataset_name=str(dataset_name or "").strip(),
        )
        return await DatasetNavigationService._generate_questions_via_llm(prompt)

    @staticmethod
    async def warm_up_navigation_caches_background() -> None:
        """后台异步静默预热数据门户缓存，最大程度消除变更后用户的首屏 LLM 延迟。"""
        import asyncio
        from datetime import datetime, timedelta
        from sqlalchemy import select
        from app.core.orm import AsyncSessionLocal
        from app.models.audit import AgentExecutionHistory
        from app.models.user import User

        logger.info("[Portal Warm-up] Background navigation cache warm-up task started.")
        try:
            active_user_ids = set()
            three_days_ago = datetime.now() - timedelta(days=3)

            async with AsyncSessionLocal() as session:
                # 1. 搜集最近 3 天有对话记录的用户 ID 字符串列表
                active_str_ids = set()
                stmt = (
                    select(AgentExecutionHistory.user_id)
                    .where(AgentExecutionHistory.created_at >= three_days_ago)
                    .where(AgentExecutionHistory.user_id.isnot(None))
                    .distinct()
                )
                res = await session.execute(stmt)
                for row in res.all():
                    val = str(row[0]).strip()
                    if val:
                        active_str_ids.add(val)

                # 2. 转换为 int 并验证用户状态为启用
                u_ids = []
                for s_id in active_str_ids:
                    try:
                        u_ids.append(int(s_id))
                    except ValueError:
                        pass

                if u_ids:
                    stmt = (
                        select(User.id)
                        .where(User.id.in_(u_ids))
                        .where(User.status == 1)
                        .where(User.role != "admin")
                    )
                    res = await session.execute(stmt)
                    for row in res.all():
                        if row[0] is not None:
                            active_user_ids.add(row[0])

            logger.info(
                "[Portal Warm-up] Detected %d active users for cache warm-up.",
                len(active_user_ids),
            )

            # 3. 并发控制与依次生成
            sem = asyncio.Semaphore(3)  # 限制最多3个并发大模型请求

            async def _warm_up_single(*, user_id: Optional[int], is_admin: bool) -> None:
                async with sem:
                    try:
                        async with AsyncSessionLocal() as session:
                            await DatasetNavigationService.build_navigation_for_user(
                                session,
                                user_id=user_id,
                                is_admin=is_admin,
                                force_refresh=True,  # 强制刷新并生成新缓存
                            )
                        logger.info(
                            "[Portal Warm-up] Cache successfully warmed up for %s.",
                            "admin" if is_admin else f"user {user_id}",
                        )
                    except Exception as ex:
                        logger.warning(
                            "[Portal Warm-up] Failed to warm up cache for %s: %s",
                            "admin" if is_admin else f"user {user_id}",
                            ex,
                        )

            # 构造全部预热任务
            tasks = []
            # 预热管理员缓存 (管理员共享一个 'admin' 键)
            tasks.append(_warm_up_single(user_id=None, is_admin=True))

            # 预热所有活跃的普通用户缓存
            for u_id in active_user_ids:
                tasks.append(_warm_up_single(user_id=u_id, is_admin=False))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            logger.info("[Portal Warm-up] Background navigation cache warm-up completed.")
        except Exception as e:
            logger.error("[Portal Warm-up] Background warm-up task encountered error: %s", e)
