"""多模态（Vision）附件与模型能力校验、用户可读错误文案。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select

from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.executors.common import _is_image_attachment

MULTIMODAL_MODEL_TYPES = frozenset({"multimodal", "vision", "image2text"})


def get_last_user_message(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for message in reversed(history):
        if message.get("role") == "user":
            return message
    return None


def last_user_message_has_images(history: List[Dict[str, Any]]) -> bool:
    """仅判断当前轮（最后一条 user 消息）是否含图片。"""
    message = get_last_user_message(history)
    if not message:
        return False
    for file_obj in message.get("files") or []:
        if _is_image_attachment(file_obj):
            return True
    return False


def history_contains_images(history: List[Dict[str, Any]]) -> bool:
    """会话历史中是否包含图片类附件（含历史轮次）。"""
    for message in history:
        if message.get("role") != "user":
            continue
        for file_obj in message.get("files") or []:
            if _is_image_attachment(file_obj):
                return True
    return False


def is_multimodal_api_error(err: str) -> bool:
    text = str(err or "")
    lower = text.lower()
    if "not a multimodal model" in lower:
        return True
    if "multimodal" in lower and any(
        token in lower for token in ("not a", "does not support", "unsupported", "non-multimodal")
    ):
        return True
    if "不支持" in text and any(token in text for token in ("多模态", "识图", "图片", "视觉")):
        return True
    return False


def _extract_model_from_error(err: str) -> Optional[str]:
    patterns = (
        r"'([^']+)'\s+is not a multimodal model",
        r'"([^"]+)"\s+is not a multimodal model',
        r"model[:\s]+([^\s,'\"]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, str(err), re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def format_execution_error(err: str, model_name: Optional[str] = None) -> str:
    """将底层异常转为用户可理解的提示。"""
    if is_multimodal_api_error(err):
        resolved = model_name or _extract_model_from_error(err) or "当前模型"
        return AgentServicePrompts.multimodal_unsupported_message(resolved)
    return AgentServicePrompts.execution_error(str(err))


async def model_supports_multimodal(model_name: Optional[str]) -> Optional[bool]:
    """
    查询模型注册表是否声明支持多模态。
    返回 None 表示未注册或未知，由上游 API 再试。
    """
    if not model_name:
        return None

    try:
        from app.core.orm import AsyncSessionLocal
        from app.models.ai_model import AIModel

        async with AsyncSessionLocal() as session:
            stmt = select(AIModel).where(
                AIModel.is_active == True,
                or_(AIModel.model_id == model_name, AIModel.name == model_name),
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return (row.type or "").lower() in MULTIMODAL_MODEL_TYPES
    except Exception:
        return None


def resolve_runtime_model_name(
    config: Any,
    *,
    prefer_synthesis: bool = True,
) -> Optional[str]:
    """与 AgentConfigProvider 优先级大致对齐，用于发送前校验。"""
    from app.core.context import get_debug_option

    debug_model = get_debug_option("model")
    if debug_model:
        return str(debug_model)
    if prefer_synthesis and getattr(config, "synthesis_model_name", None):
        return config.synthesis_model_name
    return getattr(config, "model_name", None)


async def ensure_multimodal_compatible(
    history: List[Dict[str, Any]],
    model_name: Optional[str],
) -> Optional[str]:
    """
    若**当前轮**用户消息含图片且模型明确不支持多模态，返回用户提示文案。
    历史轮次的图片不应阻塞后续纯文本追问。
    """
    if not last_user_message_has_images(history):
        return None
    if not model_name:
        return None

    supports = await model_supports_multimodal(model_name)
    if supports is False:
        return AgentServicePrompts.multimodal_unsupported_message(model_name)
    return None
