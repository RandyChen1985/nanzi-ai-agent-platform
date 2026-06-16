"""基于 ChatBI {dataset_menu} 与用户权限，由 LLM 生成数据能力导航（含 quick 追问按钮）。"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)

_NAV_CACHE_TTL_SECONDS = 600
_NAV_PROMPT_VERSION = "v2"


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


class DatasetNavigationService:
    @staticmethod
    async def _get_dataset_menu(
        *,
        user_id: Optional[int],
        is_admin: bool,
    ) -> str:
        return await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)

    @staticmethod
    async def _load_cached_navigation(cache_key: str) -> Optional[str]:
        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                cached = await redis.get(cache_key)
                if cached:
                    return str(cached)
        except Exception as e:
            logger.warning("Dataset navigation cache read failed: %s", e)
        return None

    @staticmethod
    async def _save_cached_navigation(cache_key: str, markdown: str) -> None:
        try:
            from app.core.redis import get_redis

            redis = await get_redis()
            if redis:
                await redis.set(cache_key, markdown, ex=_NAV_CACHE_TTL_SECONDS)
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
    async def build_navigation_for_user(
        db: AsyncSession,
        *,
        user_id: Optional[int],
        is_admin: bool,
    ) -> Dict[str, Any]:
        del db  # 与 ChatBI 一致，数据集目录来自 AgentConfigProvider.get_dataset_menu
        dataset_menu = await DatasetNavigationService._get_dataset_menu(
            user_id=user_id,
            is_admin=is_admin,
        )
        dataset_count = count_datasets_in_menu(dataset_menu)

        menu_hash = hashlib.md5(dataset_menu.encode("utf-8")).hexdigest()[:12]
        user_key = _user_cache_key(user_id=user_id, is_admin=is_admin)
        cache_key = f"agent:dataset_navigation:{user_key}:{menu_hash}:{_NAV_PROMPT_VERSION}"

        markdown = await DatasetNavigationService._load_cached_navigation(cache_key)
        if not markdown:
            markdown = await DatasetNavigationService._generate_navigation_markdown(dataset_menu)
            await DatasetNavigationService._save_cached_navigation(cache_key, markdown)

        return {
            "dataset_count": dataset_count,
            "groups": [],
            "markdown": markdown,
        }
