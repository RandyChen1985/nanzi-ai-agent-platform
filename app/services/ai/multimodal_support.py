"""多模态（Vision）附件与模型能力校验、用户可读错误文案。"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy import or_, select

from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.executors.common import (
    USER_MESSAGE_CONTEXT_DIVIDER,
    _is_image_attachment,
    _plain_user_text,
    convert_history_to_messages,
    has_vision_sidecar,
)

logger = logging.getLogger(__name__)

MULTIMODAL_CONFIG_KEY = "multimodal_model_name"

MULTIMODAL_MODEL_TYPES = frozenset({"multimodal", "vision", "image2text"})

_CONTEXT_WINDOW_ERROR_RE = re.compile(
    r"maximum context length of\s*([\d,]+)\s*tokens"
    r".*?total of\s*([\d,]+)\s*tokens:\s*([\d,]+)\s*tokens"
    r"\s+from the input messages and\s*([\d,]+)\s*tokens"
    r"\s+for the completion",
    re.IGNORECASE | re.DOTALL,
)


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


def is_context_window_api_error(err: str) -> bool:
    """识别模型输入加输出预留超过上下文窗口的错误。"""
    lower = str(err or "").lower()
    return "maximum context length" in lower and "requested token count" in lower


def format_context_window_error(err: str) -> str:
    """将上下文窗口超限错误转换为用户可执行的中文提示。"""
    match = _CONTEXT_WINDOW_ERROR_RE.search(str(err or ""))
    if not match:
        return "输入内容过长，已超过当前模型的上下文上限。请减少输入内容，或分批发送。"

    context_limit, total, input_tokens, completion_tokens = (
        int(value.replace(",", "")) for value in match.groups()
    )
    overflow = max(total - context_limit, 0)
    return (
        f"输入内容过长：当前模型最多支持 {context_limit:,} tokens。"
        f"本次输入约 {input_tokens:,} tokens，并预留了 {completion_tokens:,} tokens 输出，"
        f"总计 {total:,} tokens，超出上限 {overflow:,} tokens。"
        "请减少输入内容，或分批发送。"
    )


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


def unwrap_exception_message(exc: Any) -> str:
    """递归解包 ExceptionGroup 或异常链，提取最底层的具体错误信息。"""
    if exc is None:
        return ""
    if isinstance(exc, str):
        raw = exc.strip()
        if "One or more tool calls raised an exception" in raw:
            return "工具调用执行过程中发生异常"
        return raw

    exceptions = getattr(exc, "exceptions", None)
    if exceptions and isinstance(exceptions, (list, tuple)):
        sub_messages = [unwrap_exception_message(sub) for sub in exceptions if sub is not None]
        non_empty = [
            m for m in sub_messages
            if m and "One or more tool calls raised an exception" not in m
        ]
        if non_empty:
            return "；".join(non_empty)
        if sub_messages:
            return "；".join(sub_messages)

    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause and isinstance(cause, BaseException) and cause is not exc:
        cause_msg = unwrap_exception_message(cause)
        if cause_msg and cause_msg != str(exc):
            return cause_msg

    raw_msg = str(exc).strip()
    if "One or more tool calls raised an exception" in raw_msg:
        return "工具调用执行过程中发生异常"
    return raw_msg


def format_execution_error(err: Any, model_name: Optional[str] = None) -> str:
    """将底层异常转为用户可理解的提示。"""
    clean_err = unwrap_exception_message(err)
    if is_context_window_api_error(clean_err):
        return format_context_window_error(clean_err)
    if is_multimodal_api_error(clean_err):
        resolved = model_name or _extract_model_from_error(clean_err) or "当前模型"
        return AgentServicePrompts.multimodal_unsupported_message(resolved)
    return AgentServicePrompts.execution_error(clean_err)


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
    from app.core.context import get_execution_option

    request_model = get_execution_option("model")
    if request_model:
        return str(request_model)
    if prefer_synthesis and getattr(config, "synthesis_model_name", None):
        return config.synthesis_model_name
    return getattr(config, "model_name", None)


def format_vision_sidecar_block(vision_model: str, caption: str) -> str:
    caption = (caption or "").strip()
    return (
        f'<vision_sidecar model="{vision_model}">\n'
        "【图片解析】以下内容由系统默认多模态模型生成，供后续纯文本模型使用，不是用户原文。\n\n"
        f"{caption}\n"
        "</vision_sidecar>"
    )


def inject_vision_sidecar(message: Dict[str, Any], vision_model: str, caption: str) -> str:
    """把旁路解析写入用户消息 content，返回注入后的全文。"""
    block = format_vision_sidecar_block(vision_model, caption)
    content = str(message.get("content") or "").rstrip()
    if has_vision_sidecar(content):
        return content
    if not content:
        updated = block
    elif USER_MESSAGE_CONTEXT_DIVIDER in content:
        updated = f"{content}\n\n{block}"
    else:
        updated = f"{content}{USER_MESSAGE_CONTEXT_DIVIDER}{block}"
    message["content"] = updated
    return updated


async def resolve_default_multimodal_model_name() -> Optional[str]:
    """读取并校验系统默认多模态模型；未配置或类型不符时返回 None。"""
    from app.services.config_service import ConfigService

    name = (await ConfigService.get(MULTIMODAL_CONFIG_KEY) or "").strip()
    if not name:
        return None
    supports = await model_supports_multimodal(name)
    if supports is True:
        return name
    return None


def _response_text(response: Any) -> str:
    text = getattr(response, "content", None)
    if isinstance(text, list):
        parts: List[str] = []
        for item in text:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if text is None:
        return str(response or "").strip()
    return str(text).strip()


async def describe_images_with_vision_model(
    last_user: Dict[str, Any],
    vision_model: str,
) -> str:
    """用默认多模态模型解析本轮图片，只返回描述文本。"""
    from app.services.ai.config import AgentConfigProvider

    prompt = AgentServicePrompts.vision_sidecar_prompt(
        _plain_user_text(str(last_user.get("content") or ""))
    )
    messages = convert_history_to_messages(
        [
            {
                "role": "user",
                "content": prompt,
                "files": last_user.get("files") or [],
            }
        ]
    )
    if not messages:
        raise RuntimeError("未能构造识图请求")
    human = messages[0]
    content = getattr(human, "content", None)
    has_image = isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )
    if not has_image:
        raise RuntimeError("未能读取图片附件")

    llm = await AgentConfigProvider.get_configured_llm(
        streaming=False,
        model_override=vision_model,
        temp_override=0.0,
    )
    response = await llm.ainvoke([human])
    text = _response_text(response)
    if not text:
        raise RuntimeError("多模态模型未返回图片解析结果")
    return text


async def _persist_vision_sidecar(
    user_id: Optional[str],
    conversation_id: Optional[str],
    content: str,
) -> None:
    if not user_id or not conversation_id:
        return
    try:
        from app.services.ai.memory_service import memory_service

        await memory_service.update_last_user_message_content(
            user_id,
            conversation_id,
            content,
        )
    except Exception:
        logger.warning("Failed to persist vision sidecar into conversation history", exc_info=True)


def _vision_log_event(status: str, details: str, log_id: str) -> Dict[str, Any]:
    return {
        "type": "log",
        "id": log_id,
        "title": "解析图片",
        "details": details,
        "status": status,
        "category": "vision",
    }


async def run_multimodal_gate(
    history: List[Dict[str, Any]],
    model_name: Optional[str],
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    本轮含图且当前模型不支持多模态时：
    - 有默认多模态则旁路看图、注入文字、告知用户；
    - 否则 yield status=error 的拦截提示。
    调用方遇到无 type 的 error 事件后应停止本轮执行。
    """
    last_user = get_last_user_message(history)
    if not last_user or not last_user_message_has_images(history):
        return
    if has_vision_sidecar(last_user.get("content")):
        return
    if not model_name:
        return

    supports = await model_supports_multimodal(model_name)
    if supports is not False:
        return

    vision_model = await resolve_default_multimodal_model_name()
    if not vision_model:
        yield {
            "content": AgentServicePrompts.multimodal_unsupported_message(model_name),
            "status": "error",
        }
        return

    log_id = f"vision_sidecar_{uuid.uuid4().hex[:8]}"
    yield _vision_log_event(
        "pending",
        f"当前模型 {model_name} 不支持识图，正在使用系统默认多模态模型 {vision_model} 解析图片。",
        log_id,
    )
    try:
        caption = await describe_images_with_vision_model(last_user, vision_model)
        updated = inject_vision_sidecar(last_user, vision_model, caption)
        await _persist_vision_sidecar(user_id, conversation_id, updated)
    except Exception as exc:
        logger.warning("Vision sidecar failed for model %s: %s", vision_model, exc)
        yield _vision_log_event("error", f"系统默认多模态模型 {vision_model} 解析失败。", log_id)
        yield {
            "content": AgentServicePrompts.multimodal_sidecar_failed_message(
                model_name,
                vision_model,
                str(exc),
            ),
            "status": "error",
        }
        return

    yield _vision_log_event(
        "success",
        f"已使用系统默认多模态模型 {vision_model} 完成图片解析，本轮仍由 {model_name} 继续回答。",
        log_id,
    )
    yield {
        "content": AgentServicePrompts.multimodal_sidecar_notice(model_name, vision_model),
        "status": "success",
    }


async def ensure_multimodal_compatible(
    history: List[Dict[str, Any]],
    model_name: Optional[str],
) -> Optional[str]:
    """
    兼容旧调用：若当前轮含图且模型不支持多模态、又无法旁路，返回拦截文案。
    新代码请使用 run_multimodal_gate。
    """
    if not last_user_message_has_images(history):
        return None
    if not model_name:
        return None
    if has_vision_sidecar((get_last_user_message(history) or {}).get("content")):
        return None

    supports = await model_supports_multimodal(model_name)
    if supports is not False:
        return None
    if await resolve_default_multimodal_model_name():
        return None
    return AgentServicePrompts.multimodal_unsupported_message(model_name)
