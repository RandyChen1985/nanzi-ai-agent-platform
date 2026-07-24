"""Version-scoped welcome card validation and AI recommendation."""

from __future__ import annotations

import json
import hashlib
import logging
import re
import secrets
from typing import Any

from app.core.redis import get_redis
from app.services.ai.config import AgentConfigProvider
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages


WELCOME_CARD_ICONS = {"chart", "knowledge", "workspace", "report", "alert", "chat"}
WELCOME_CARD_CACHE_TTL_SECONDS = 300
logger = logging.getLogger(__name__)


def normalize_welcome_config(raw: Any) -> dict[str, Any]:
    """Return a safe, display-ready welcome configuration for one agent version."""
    raw = raw if isinstance(raw, dict) else {}
    enabled = bool(raw.get("enabled", False))
    mode = str(raw.get("mode") or "manual").strip().lower()
    if mode not in {"manual", "ai"}:
        mode = "manual"
    requirement = str(raw.get("generation_requirement") or "").strip()[:500]
    cards: list[dict[str, str]] = []
    for item in raw.get("cards") or []:
        if not isinstance(item, dict):
            continue
        icon = str(item.get("icon") or "chat").strip().lower()
        if icon not in WELCOME_CARD_ICONS:
            icon = "chat"
        card = {
            "icon": icon,
            "title": str(item.get("title") or "").strip()[:40],
            "subtitle": str(item.get("subtitle") or "").strip()[:100],
            "prompt": str(item.get("prompt") or "").strip()[:300],
        }
        if all(card[key] for key in ("title", "subtitle", "prompt")):
            cards.append(card)
    if mode == "ai":
        cards = []
    elif enabled and len(cards) != 3:
        raise ValueError("开启欢迎语设置时必须配置完整的 3 张欢迎卡片")
    return {
        "enabled": enabled,
        "mode": mode,
        "generation_requirement": requirement,
        "cards": cards[:3],
    }


def safe_welcome_config(raw: Any) -> dict[str, Any]:
    """Return a display-safe config without letting malformed legacy JSON break chat startup."""
    try:
        return normalize_welcome_config(raw)
    except ValueError:
        return normalize_welcome_config(None)


async def generate_welcome_cards(
    *,
    name: str,
    display_name: str,
    description: str,
    system_prompt: str,
    generation_requirement: str,
    variation_seed: str = "",
) -> list[dict[str, str]]:
    """Generate three cards only; persistence remains an explicit editor save."""
    prompt = f"""你是企业智能体欢迎页文案设计师。请为一个智能体生成恰好 3 张欢迎卡片。
智能体名称：{display_name or name}
标识：{name}
业务描述：{description or '无'}
系统提示词摘要：{system_prompt[:1200] or '无'}
额外要求：{generation_requirement or '无'}
本次推荐创意种子：{variation_seed or '默认'}

每张卡片必须给用户一个可以立即点击执行的、与业务强相关的问题。只返回 JSON 对象：
{{"cards":[{{"icon":"chart|knowledge|workspace|report|alert|chat","title":"不超过 16 字","subtitle":"不超过 40 字","prompt":"用户点击后发送的完整问题"}}]}}
禁止 Markdown，禁止输出任何解释。"""
    llm = await AgentConfigProvider.get_configured_llm(streaming=False)
    content = await chat_client_from_handle(llm).generate_text(
        system_user_prompt_messages(prompt, user_prompt="请生成 3 张欢迎卡片。")
    )
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(content or "").strip(), flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("推荐生成未返回可解析的卡片 JSON，请重试") from exc
    return normalize_welcome_config({"enabled": True, "mode": "manual", "cards": payload.get("cards")}).get("cards", [])


def _runtime_welcome_cache_key(config: Any, requirement: str) -> str:
    identity = "|".join((
        str(getattr(config, "agent_id", "") or ""),
        str(getattr(config, "agent_version", "") or ""),
        requirement,
    ))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"agent:welcome_cards:v1:{digest}"


async def get_runtime_welcome_cards(config: Any) -> list[dict[str, str]]:
    """Return fixed manual cards or 5-minute cached AI recommendations for one active LOCAL version."""
    if str(getattr(config, "engine_type", "LOCAL") or "LOCAL").upper() != "LOCAL":
        return []
    welcome_config = safe_welcome_config(getattr(config, "welcome_config", None))
    if not welcome_config["enabled"]:
        return []
    if welcome_config["mode"] == "manual":
        return welcome_config["cards"]

    cache_key = _runtime_welcome_cache_key(config, welcome_config["generation_requirement"])
    redis = None
    try:
        redis = await get_redis()
        if redis:
            cached = await redis.get(cache_key)
            if cached:
                if isinstance(cached, bytes):
                    cached = cached.decode("utf-8")
                return normalize_welcome_config({"enabled": True, "mode": "manual", "cards": json.loads(cached)}).get("cards", [])
    except Exception as exc:
        logger.warning("Failed to read welcome-card cache: %s", exc)

    try:
        cards = await generate_welcome_cards(
            name=str(getattr(config, "agent_name", "") or ""),
            display_name=str(getattr(config, "agent_display_name", "") or ""),
            description=str(getattr(config, "description", "") or ""),
            system_prompt=str(getattr(config, "system_prompt", "") or ""),
            generation_requirement=welcome_config["generation_requirement"],
            variation_seed=secrets.token_hex(8),
        )
    except Exception as exc:
        logger.warning("Failed to generate runtime welcome cards: %s", exc)
        return []

    if redis:
        try:
            await redis.set(cache_key, json.dumps(cards, ensure_ascii=False), ex=WELCOME_CARD_CACHE_TTL_SECONDS)
        except Exception as exc:
            logger.warning("Failed to write welcome-card cache: %s", exc)
    return cards
