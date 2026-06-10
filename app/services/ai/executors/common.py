"""Executor 公共工具：历史转换、Token 提取、XML 工具解析。"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, TypeVar

from app.services.ai.runtime.agentscope.compat import AIMessage, BaseMessage, HumanMessage, SystemMessage
from app.services.ai.executors.prompts import SharedPrompts
from app.utils.fs_paths import get_data_base_dir, normalize_under_base

logger = logging.getLogger(__name__)

# 流式 LLM 输出失败时的最大尝试次数（含首次）
MODEL_STREAM_MAX_RETRIES = 2

_TRANSIENT_STREAM_KEYWORDS = (
    "connection",
    "timeout",
    "reset",
    "index out of range",
    "unexpected",
    "temporarily unavailable",
    "502",
    "503",
    "504",
)

T = TypeVar("T")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
USER_MESSAGE_CONTEXT_DIVIDER = "\n\n---\n\n"


def _plain_user_text(content: str) -> str:
    """历史轮次仅保留用户可见纯文字，剥离附件系统指令块。"""
    raw = content or ""
    idx = raw.find(USER_MESSAGE_CONTEXT_DIVIDER)
    if idx == -1:
        return raw.strip()
    return raw[:idx].strip()


def _normalize_file_ext(ext: str = "", url: str = "") -> str:
    if not ext and url:
        ext = os.path.splitext(url)[1]
    ext = ext.lower().strip()
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return ext


def _is_image_attachment(file_obj: Dict[str, Any]) -> bool:
    if file_obj.get("type") in ("skill", "knowledge_base", "local_dir"):
        return False
    return _normalize_file_ext(file_obj.get("ext", ""), file_obj.get("url", "")) in IMAGE_EXTENSIONS


def _resolve_image_local_path(file_obj: Dict[str, Any]) -> Optional[str]:
    url = file_obj.get("url", "")
    if not url:
        return None

    if url.startswith("/static/uploads/"):
        local_path = os.path.join("data", "uploads", os.path.basename(url))
        return local_path if os.path.isfile(local_path) else None

    if file_obj.get("type") == "local_file" or os.path.isabs(url):
        safe_path = normalize_under_base(url, get_data_base_dir())
        if safe_path and os.path.isfile(safe_path):
            return safe_path

    return None


def _encode_image_data_url(local_path: str, ext: str = "") -> Optional[str]:
    if not ext:
        ext = os.path.splitext(local_path)[1]
    ext_cleaned = _normalize_file_ext(ext).lstrip(".")
    if ext_cleaned == "jpg":
        ext_cleaned = "jpeg"
    try:
        with open(local_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return f"data:image/{ext_cleaned};base64,{encoded_string}"
    except Exception as e:
        logger.warning("Failed to read local image for vision: %s", e)
        return None


def _attachment_abs_path(file_obj: Dict[str, Any]) -> str:
    url = file_obj.get("url", "")
    ftype = file_obj.get("type")
    if ftype in ("local_file", "local_dir"):
        return url
    if url.startswith("/static/uploads/"):
        return f"/app/data/uploads/{os.path.basename(url)}"
    return url or "/app/data/uploads/unknown"


def is_retryable_stream_error(exc: Exception) -> bool:
    """判断流式输出异常是否属于可重试的 transient 错误。"""
    if isinstance(exc, (ConnectionError, TimeoutError, IndexError, KeyError, ValueError, RuntimeError)):
        return True
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _TRANSIENT_STREAM_KEYWORDS)


def build_stream_retry_log(exc: Exception, attempt: int, max_retries: int = MODEL_STREAM_MAX_RETRIES) -> Dict[str, Any]:
    wait_time = 2 ** attempt
    return {
        "type": "log",
        "id": f"retry_{uuid.uuid4().hex[:8]}",
        "title": "⚠️ 模型响应异常，正在重试",
        "details": (
            f"流式输出中断: {str(exc)}。"
            f" {wait_time}s 后进行第 {attempt + 2} 次尝试..."
        ),
        "status": "warning",
    }


def build_stream_error_log(exc: Exception, *, title: str = "⚠️ 模型响应异常") -> Dict[str, Any]:
    return {
        "type": "log",
        "id": f"err_{uuid.uuid4().hex[:8]}",
        "title": title,
        "details": f"流式输出中断: {str(exc)}",
        "status": "error",
    }


async def stream_with_retry(
    stream_factory: Callable[[], AsyncIterator[T]],
    *,
    max_retries: int = MODEL_STREAM_MAX_RETRIES,
    allow_retry: Callable[[], bool] = lambda: True,
) -> AsyncIterator[T]:
    """对 async stream 做整流重试；仅在 allow_retry() 为真且错误可重试时生效。"""
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            async for item in stream_factory():
                yield item
            return
        except Exception as exc:
            last_err = exc
            should_retry = (
                attempt < max_retries - 1
                and allow_retry()
                and is_retryable_stream_error(exc)
            )
            if not should_retry:
                raise
            await asyncio.sleep(2 ** attempt)
    if last_err:
        raise last_err


def extract_tokens_from_message(msg: Any) -> dict:
    """从 runtime message/chunk 提取 token 用量。"""
    res = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not msg:
        return res
    if hasattr(msg, "usage_metadata") and msg.usage_metadata:
        um = msg.usage_metadata
        res["prompt_tokens"] = um.get("input_tokens") or 0
        res["completion_tokens"] = um.get("output_tokens") or 0
        res["total_tokens"] = um.get("total_tokens") or (
            res["prompt_tokens"] + res["completion_tokens"]
        )
        return res
    if hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
        tu = msg.response_metadata.get("token_usage")
        if isinstance(tu, dict):
            res["prompt_tokens"] = tu.get("prompt_tokens") or tu.get("input_tokens") or 0
            res["completion_tokens"] = tu.get("completion_tokens") or tu.get("output_tokens") or 0
            res["total_tokens"] = tu.get("total_tokens") or (
                res["prompt_tokens"] + res["completion_tokens"]
            )
            return res
    return res


def structurize_user_content(content: str) -> str:
    """将前端通过 '---' 拼接的系统注入数据进行结构化 XML 包裹，以提升隔离度与安全性。"""
    if not content:
        return ""
    if "---" not in content:
        return content
    parts = content.split("---", 1)
    user_text = parts[0].strip()
    system_injected = parts[1].strip()
    if not system_injected:
        return content
    
    return (
        f"{user_text}\n\n"
        f"<system_injected_attachments>\n"
        f"{system_injected}\n"
        f"</system_injected_attachments>"
    )


def convert_history_to_messages(history: List[Dict[str, str]]) -> List[BaseMessage]:
    """将平台 messages 转为 runtime BaseMessage 列表（含附件/多模态）。"""
    messages: List[BaseMessage] = []
    last_user_idx: Optional[int] = None
    for idx in range(len(history) - 1, -1, -1):
        if history[idx].get("role") == "user":
            last_user_idx = idx
            break

    for idx, m in enumerate(history):
        role = m["role"]
        content = m["content"]
        if role == "user":
            # 历史轮：仅纯文字，不带附件/图片/系统指令
            if idx != last_user_idx:
                messages.append(HumanMessage(content=_plain_user_text(content)))
                continue

            files = m.get("files")
            img_files: List[Dict[str, Any]] = []
            non_img_files: List[Dict[str, Any]] = []
            if files:
                for f in files:
                    if f.get("type") in ("skill", "knowledge_base"):
                        continue
                    if _is_image_attachment(f):
                        img_files.append(f)
                    else:
                        non_img_files.append(f)

            attachment_prompt = ""
            if non_img_files:
                lines = [
                    "",
                    "<backend_injected_attachments>",
                    "用户随附上传了非图片附件信息，已保存在服务器：",
                ]
                for f in non_img_files:
                    filename = f.get("filename", "未知文件")
                    size_str = (
                        f"{(f.get('size', 0) / 1024):.1f} KB" if f.get("size") else "未知大小"
                    )
                    abs_path = _attachment_abs_path(f)
                    lines.append(f"- 文件名: {filename} (大小: {size_str})")
                    lines.append(f"  服务器内绝对路径: {abs_path}")
                lines.append("</backend_injected_attachments>")
                attachment_prompt = "\n".join(lines)

            final_text = structurize_user_content(content) + attachment_prompt

            if img_files:
                multimodal_content = [{"type": "text", "text": final_text}]
                for f in img_files:
                    local_path = _resolve_image_local_path(f)
                    base64_data = (
                        _encode_image_data_url(local_path, f.get("ext", ""))
                        if local_path
                        else None
                    )
                    img_url = base64_data or f.get("url", "")
                    if img_url:
                        multimodal_content.append(
                            {"type": "image_url", "image_url": {"url": img_url}}
                        )
                messages.append(HumanMessage(content=multimodal_content))
            else:
                messages.append(HumanMessage(content=final_text))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
        elif role == "system":
            messages.append(SystemMessage(content=content))
    return messages


def _system_content_text(message: SystemMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def normalize_messages_for_llm(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Ensure provider-compatible ordering: all system instructions live at index 0."""
    system_parts: List[str] = []
    non_system_messages: List[BaseMessage] = []
    saw_system = False

    for message in messages:
        if isinstance(message, SystemMessage):
            saw_system = True
            content = _system_content_text(message)
            if content:
                system_parts.append(content)
        else:
            non_system_messages.append(message)

    # 用高度结构化的双横线与标识符，在单一 SystemMessage 中做强边界区隔
    formatted_parts = []
    for idx, part in enumerate(system_parts, 1):
        # 探测是哪种类型的系统消息，以加上结构化 Markdown 标题与 XML 区隔（兼容多系统消息，提升注意力聚焦与 API 兼容）
        title = "系统指令"
        if "[云枢智能体平台 · 全局守则]" in part:
            title = "平台守则与安全约束"
        elif "# Active User Profile" in part:
            title = "当前用户画像"
        elif "# Session Runtime Context" in part:
            title = "会话运行时上下文"
        elif "[Active Skills Loaded]" in part:
            title = "已匹配挂载技能"
        elif "[Memory Profile]" in part:
            title = "长期记忆设定"
        elif "[System Preloaded Memories]" in part:
            title = "主动预加载记忆"

        formatted_parts.append(
            f"<!-- SYSTEM_BLOCK_START: {title} -->\n"
            f"=========================================\n"
            f"## SECTION {idx}: {title}\n"
            f"=========================================\n"
            f"{part}\n"
            f"<!-- SYSTEM_BLOCK_END: {title} -->"
        )

    merged_system = SystemMessage(content="\n\n".join(formatted_parts))
    return [merged_system] + non_system_messages


def append_system_instruction(messages: List[BaseMessage], content: str) -> None:
    """Append a runtime instruction without creating a mid-conversation SystemMessage."""
    instruction = (content or "").strip()
    if not instruction:
        return
    messages.append(SystemMessage(content=instruction))
    messages[:] = normalize_messages_for_llm(messages)


def parse_xml_tool_calls(content: str) -> List[Dict[str, Any]]:
    """解析模型输出的 <function_calls> XML 工具调用块。"""
    tool_calls: List[Dict[str, Any]] = []
    match = re.search(r"<function_calls>(.*?)</function_calls>", content, re.DOTALL | re.IGNORECASE)
    if not match:
        match = re.search(r"<function_calls>(.*)", content, re.DOTALL | re.IGNORECASE)
    if not match:
        return tool_calls
    xml_content = match.group(0)
    try:
        from xml.etree import ElementTree as ET

        fixed_xml = xml_content.replace("</invokefunction_calls>", "</invoke></function_calls>")
        if not fixed_xml.endswith("</function_calls>"):
            fixed_xml += "</function_calls>"
        root = ET.fromstring(fixed_xml)
        for invoke in root.findall("invoke"):
            name = invoke.get("name")
            args = {}
            for param in invoke.findall("parameter"):
                p_name = param.get("name")
                p_value = param.text
                if p_name:
                    args[p_name] = p_value
            if name:
                tool_calls.append({"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"})
    except Exception:
        pass
    return tool_calls


def tools_include_named(tools: List[Any], tool_name: str) -> bool:
    for t in tools or []:
        if getattr(t, "name", None) == tool_name:
            return True
    return False
