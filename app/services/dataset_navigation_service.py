"""基于 ChatBI {dataset_menu} 与用户权限，由 LLM 生成我的数据门户（含 quick 追问按钮）。"""
from __future__ import annotations

import json
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)

_NAV_CACHE_TTL_SECONDS = 600
_NAV_PROMPT_VERSION = "v4"
_NAV_CACHE_GEN_KEY = "agent:dataset_navigation:cache_generation"
_CLICK_STATS_TTL_SECONDS = 90 * 24 * 60 * 60


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


class DatasetNavigationService:
    @staticmethod
    async def _get_dataset_menu(
        *,
        user_id: Optional[int],
        is_admin: bool,
    ) -> str:
        return await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)

    @staticmethod
    async def _get_navigation_cache_generation() -> str:
        try:
            redis = await get_redis()
            if redis:
                cached = await redis.get(_NAV_CACHE_GEN_KEY)
                if cached is not None:
                    return str(cached)
        except Exception as e:
            logger.warning("Dataset navigation cache generation read failed: %s", e)
        return "0"

    @staticmethod
    async def bump_navigation_cache_generation() -> None:
        """元数据变更后递增，使门户 Markdown 缓存失效。"""
        try:
            redis = await get_redis()
            if redis:
                await redis.incr(_NAV_CACHE_GEN_KEY)
        except Exception as e:
            logger.warning("Dataset navigation cache generation bump failed: %s", e)

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
    async def _generate_navigation_markdown(dataset_menu: str) -> str:
        if not menu_has_authorized_datasets(dataset_menu):
            return DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)

        fallback = DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)
        try:
            llm = await AgentConfigProvider.get_configured_llm(streaming=False)
            chat_client = chat_client_from_handle(llm)
            content = await chat_client.generate_text(
                [
                    RuntimeMessage(
                        role="system",
                        content=[
                            RuntimeContentBlock(
                                type="text",
                                text=DataQueryPrompts.dataset_navigation_generation_prompt(dataset_menu),
                            )
                        ],
                    )
                ]
            )
            cleaned = str(content or "").strip()
            if cleaned and DataQueryPrompts.has_quick_suggestions(cleaned):
                return finalize_visible_reply(cleaned, collapse_duplicates=False)
        except Exception as e:
            logger.warning("Dataset navigation LLM generation failed: %s", e)
        return finalize_visible_reply(fallback, collapse_duplicates=False)

    @staticmethod
    def _click_rank_key(*, user_key: str, dataset_menu_hash: str) -> str:
        return f"agent:dataset_navigation_click_rank:{user_key}:{dataset_menu_hash}"

    @staticmethod
    def _click_meta_key(*, user_key: str, dataset_menu_hash: str) -> str:
        return f"agent:dataset_navigation_click_meta:{user_key}:{dataset_menu_hash}"

    @staticmethod
    async def _load_question_click_stats(
        *,
        user_key: str,
        dataset_menu_hash: str,
    ) -> Dict[str, Dict[str, Any]]:
        try:
            redis = await get_redis()
            if not redis:
                return {}
            rank_key = DatasetNavigationService._click_rank_key(
                user_key=user_key,
                dataset_menu_hash=dataset_menu_hash,
            )
            meta_key = DatasetNavigationService._click_meta_key(
                user_key=user_key,
                dataset_menu_hash=dataset_menu_hash,
            )
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
        dataset_menu_hash: str,
        query: str,
        label: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> None:
        clean_hash = str(dataset_menu_hash or "").strip()
        clean_query = str(query or "").strip()
        if not clean_hash or not clean_query:
            return
        try:
            redis = await get_redis()
            if not redis:
                return
            user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
            question_id = _question_hash(clean_query)
            rank_key = DatasetNavigationService._click_rank_key(
                user_key=user_key,
                dataset_menu_hash=clean_hash,
            )
            meta_key = DatasetNavigationService._click_meta_key(
                user_key=user_key,
                dataset_menu_hash=clean_hash,
            )
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
        dataset_menu_hash: str,
        query: str,
    ) -> bool:
        clean_hash = str(dataset_menu_hash or "").strip()
        clean_query = str(query or "").strip()
        if not clean_hash or not clean_query:
            return False
        try:
            redis = await get_redis()
            if not redis:
                return False
            user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
            question_id = _question_hash(clean_query)
            rank_key = DatasetNavigationService._click_rank_key(
                user_key=user_key,
                dataset_menu_hash=clean_hash,
            )
            meta_key = DatasetNavigationService._click_meta_key(
                user_key=user_key,
                dataset_menu_hash=clean_hash,
            )
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
                if "/dataset_menu" in query or "重新查看" in label:
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
                if "/dataset_menu" in query or "重新查看" in label:
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
        )
        dataset_count = count_datasets_in_menu(dataset_menu)

        menu_hash = hashlib.md5(dataset_menu.encode("utf-8")).hexdigest()[:12]
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        cache_gen = await DatasetNavigationService._get_navigation_cache_generation()
        groups = DataQueryPrompts.build_dataset_navigation_groups(dataset_menu)
        cache_key = (
            f"agent:dataset_navigation:{user_key}:{menu_hash}:{_NAV_PROMPT_VERSION}:{cache_gen}"
        )
        has_datasets = menu_has_authorized_datasets(dataset_menu)

        from_cache = False
        markdown = None if force_refresh else await DatasetNavigationService._load_cached_navigation(cache_key)
        if markdown:
            from_cache = True
        if not markdown:
            markdown = await DatasetNavigationService._generate_navigation_markdown(dataset_menu)
            
            # 检测是否因大模型报错或生成失败而降级到了兜底模板
            fallback_raw = DataQueryPrompts.build_dataset_navigation_fallback(dataset_menu)
            fallback_md = finalize_visible_reply(fallback_raw, collapse_duplicates=False)
            is_fallback = (markdown == fallback_md)
            
            # 如果是异常兜底（有授权数据集但生成了兜底文本），只缓存 15 秒，避免长期卡在兜底状态；如果是正常情况则缓存 10 分钟
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
            dataset_menu_hash=menu_hash,
        )
        groups = DatasetNavigationService._apply_question_click_stats(groups, click_stats)

        return {
            "dataset_count": dataset_count,
            "dataset_menu_hash": menu_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "groups": groups,
            "markdown": markdown,
            "is_fallback": is_fallback,
            "has_datasets": has_datasets,
            "from_cache": from_cache,
        }

    @staticmethod
    async def refresh_group_questions(
        db: AsyncSession,
        *,
        group_title: str,
        tables: list[str],
    ) -> list[dict[str, Any]]:
        """针对特定的卡片场景 (group_title) 和关联表列表 (tables)，在线实时调用大模型生成 3 个新问题"""
        table_physical_names, table_to_columns = await DatasetNavigationService._load_table_metadata(db, tables)

        prompt = DataQueryPrompts.build_group_questions_refresh_prompt(
            group_title=group_title,
            tables=tables,
            table_to_columns=table_to_columns,
            table_physical_names=table_physical_names,
        )
        return await DatasetNavigationService._generate_questions_via_llm(prompt)

    @staticmethod
    async def refresh_group_followups(
        db: AsyncSession,
        *,
        group_title: str,
        tables: list[str],
    ) -> list[dict[str, Any]]:
        """针对场景卡片生成 2 条「继续追问」探索性问题。"""
        table_physical_names, table_to_columns = await DatasetNavigationService._load_table_metadata(db, tables)
        prompt = DataQueryPrompts.build_group_followups_refresh_prompt(
            group_title=group_title,
            tables=tables,
            table_to_columns=table_to_columns,
            table_physical_names=table_physical_names,
        )
        questions = await DatasetNavigationService._generate_questions_via_llm(prompt)
        return questions[:2]

    @staticmethod
    def _parse_quick_questions_from_llm(content: str) -> list[dict[str, Any]]:
        cleaned = str(content or "").strip()
        questions: list[dict[str, Any]] = []
        pattern = r"-\s*\[(?:[^\]]*🙋\s*)?([^\]]+)\]\(quick:([^\)]+)\)"
        for match in re.finditer(pattern, cleaned):
            label = match.group(1).strip()
            query = match.group(2).strip()
            if "/dataset_menu" in query or "重新查看" in label:
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
                [
                    RuntimeMessage(
                        role="system",
                        content=[RuntimeContentBlock(type="text", text=prompt)],
                    )
                ]
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

