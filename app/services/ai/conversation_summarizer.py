"""LLM-based conversation summary for memory index."""
import json
import logging
import re
import asyncio
from typing import Any, Dict, List

from app.core.llm.client import get_llm_async
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是会话摘要助手。根据对话记录生成简洁的中文摘要，供跨会话记忆检索使用。"
    "输出必须是 JSON 对象，包含："
    "title（短标题，20字内）、summary（200-500字，保留关键事实与结论）、"
    "key_facts（事实列表）、decisions（已确认决策列表）、open_items（未完成事项列表）、"
    "entities（项目名、模块名、文件名、业务对象等关键词列表）、memory_type（project/task/debug/personal/general）。"
    "只保留对后续跨会话检索和继续工作有帮助的信息，不要编造对话中未出现的信息。"
)

DAILY_SYSTEM_PROMPT = (
    "你是每日记忆汇总助手。根据同一天的多条会话摘要，生成当天整体记忆。"
    "输出必须是 JSON 对象，包含：title（20字内）、summary（200-500字）、topics、decisions、open_items、entities。"
    "合并重复主题，突出当天进展、关键决定和仍需跟进的事项，不要编造输入中未出现的信息。"
)


class ConversationSummarizer:
    @staticmethod
    def _is_retryable_llm_error(err: Exception) -> bool:
        """
        Best-effort classifier for transient network/proxy/provider errors.
        We keep this local to avoid hard dependency on optional provider packages.
        """
        if isinstance(err, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            return True
        # httpx / httpcore (optional)
        try:
            import httpx  # type: ignore

            if isinstance(err, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
                return True
        except Exception:
            pass
        try:
            import httpcore  # type: ignore

            if isinstance(err, (httpcore.ConnectError, httpcore.ReadTimeout, httpcore.WriteTimeout, httpcore.PoolTimeout)):
                return True
        except Exception:
            pass
        # openai (optional)
        try:
            import openai  # type: ignore

            if isinstance(err, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError)):
                return True
        except Exception:
            pass

        msg = str(err).lower()
        retry_markers = (
            "connection error",
            "connect error",
            "timed out",
            "timeout",
            "connection refused",
            "temporary failure",
            "proxy",
            "502",
            "503",
            "504",
            "rate limit",
        )
        return any(m in msg for m in retry_markers)

    @staticmethod
    async def _generate_with_retry(chat_client: Any, messages: List[RuntimeMessage], *, max_retries: int = 3) -> str:
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await chat_client.generate_text(messages)
            except Exception as e:
                last_err = e
                if attempt >= max_retries - 1 or not ConversationSummarizer._is_retryable_llm_error(e):
                    raise
                delay = min(8.0, 1.0 * (2**attempt))
                logger.warning(
                    "[ConversationSummarizer] LLM call failed, will retry in %.1fs (attempt %s/%s): %s",
                    delay,
                    attempt + 1,
                    max_retries,
                    e,
                )
                await asyncio.sleep(delay)
        if last_err:
            raise last_err
        raise RuntimeError("Unexpected retry loop termination")

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        return [str(value)]

    @staticmethod
    def _normalize_summary(data: Dict[str, Any], raw: str) -> Dict[str, Any]:
        return {
            "title": str(data.get("title") or "会话摘要")[:80],
            "summary": str(data.get("summary") or raw)[:2000],
            "key_facts": ConversationSummarizer._as_list(data.get("key_facts")),
            "decisions": ConversationSummarizer._as_list(data.get("decisions")),
            "open_items": ConversationSummarizer._as_list(data.get("open_items")),
            "entities": ConversationSummarizer._as_list(data.get("entities")),
            "memory_type": str(data.get("memory_type") or "general")[:40],
        }

    @staticmethod
    def _normalize_daily(data: Dict[str, Any], raw: str) -> Dict[str, Any]:
        return {
            "title": str(data.get("title") or "每日记忆")[:80],
            "summary": str(data.get("summary") or raw)[:2000],
            "topics": ConversationSummarizer._as_list(data.get("topics")),
            "decisions": ConversationSummarizer._as_list(data.get("decisions")),
            "open_items": ConversationSummarizer._as_list(data.get("open_items")),
            "entities": ConversationSummarizer._as_list(data.get("entities")),
        }

    @staticmethod
    def _format_messages(messages: List[Dict[str, Any]], max_chars: int = 12000) -> str:
        lines = []
        for m in messages[-40:]:
            role = m.get("role", "user")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"{role}: {content[:2000]}")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[-max_chars:]
        return text

    @staticmethod
    async def summarize(messages: List[Dict[str, Any]]) -> Dict[str, str]:
        transcript = ConversationSummarizer._format_messages(messages)
        if not transcript.strip():
            return {"title": "空会话", "summary": "暂无对话内容。"}

        llm = await get_llm_async(streaming=False, temperature=0.2)
        if not llm:
            return ConversationSummarizer._normalize_summary(
                {"summary": transcript[:500]}, transcript[:500]
            )

        chat_client = chat_client_from_handle(llm)
        raw = await ConversationSummarizer._generate_with_retry(
            chat_client,
            [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=SYSTEM_PROMPT)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=f"请为以下对话生成摘要：\n\n{transcript}")],
                ),
            ],
            max_retries=3,
        )
        try:
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                return ConversationSummarizer._normalize_summary(data, raw)
        except json.JSONDecodeError:
            logger.warning("[ConversationSummarizer] JSON parse failed, using raw text")
        return ConversationSummarizer._normalize_summary({}, raw[:2000])

    @staticmethod
    async def summarize_daily(session_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not session_summaries:
            return {
                "title": "每日记忆",
                "summary": "当天暂无可汇总的会话摘要。",
                "topics": [],
                "decisions": [],
                "open_items": [],
                "entities": [],
            }

        lines = []
        for item in session_summaries[-50:]:
            parts = [
                f"title: {item.get('title') or ''}",
                f"summary: {item.get('summary') or ''}",
                f"key_facts: {item.get('key_facts') or ''}",
                f"decisions: {item.get('decisions') or ''}",
                f"open_items: {item.get('open_items') or ''}",
                f"entities: {item.get('entities') or ''}",
            ]
            lines.append("\n".join(parts))
        transcript = "\n\n---\n\n".join(lines)
        if len(transcript) > 12000:
            transcript = transcript[-12000:]

        llm = await get_llm_async(streaming=False, temperature=0.2)
        if not llm:
            return ConversationSummarizer._normalize_daily(
                {"summary": transcript[:500]}, transcript[:500]
            )

        chat_client = chat_client_from_handle(llm)
        raw = await ConversationSummarizer._generate_with_retry(
            chat_client,
            [
                RuntimeMessage(
                    role="system",
                    content=[RuntimeContentBlock(type="text", text=DAILY_SYSTEM_PROMPT)],
                ),
                RuntimeMessage(
                    role="user",
                    content=[RuntimeContentBlock(type="text", text=f"请汇总以下同一天的会话摘要：\n\n{transcript}")],
                ),
            ],
            max_retries=3,
        )
        try:
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group())
                return ConversationSummarizer._normalize_daily(data, raw)
        except json.JSONDecodeError:
            logger.warning("[ConversationSummarizer] daily JSON parse failed, using raw text")
        return ConversationSummarizer._normalize_daily({}, raw[:2000])
