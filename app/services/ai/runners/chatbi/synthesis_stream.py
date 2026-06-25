"""Shared LLM synthesis streaming helpers for ChatBI."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.common import extract_tokens_from_message, normalize_messages_for_llm
from app.services.ai.runtime.agentscope.stream_reconcile import finalize_visible_reply

logger = logging.getLogger(__name__)


@dataclass
class SynthesisStreamState:
    full_content: str = ""
    last_chunk: Any = None
    content_emitted: bool = False
    generation_start: Optional[float] = None
    final_llm: Any = None
    gen_log_id: str = field(default_factory=lambda: f"gen_{uuid.uuid4().hex[:8]}")


def truncate_result_dict_for_synthesis(
    result: Dict[str, Any],
    *,
    max_rows: int = 50,
) -> Dict[str, Any]:
    safe_result = dict(result)
    for key in ("rows", "items", "data", "records"):
        val = safe_result.get(key)
        if isinstance(val, list) and len(val) > max_rows:
            safe_result[key] = val[:max_rows]
            safe_result["_display_note"] = "部分明细数据由于上下文长度限制已在此处被省略..."
            break
    return safe_result


def result_dict_to_json(
    result: Dict[str, Any],
    *,
    max_chars: Optional[int] = None,
) -> str:
    text = json.dumps(
        truncate_result_dict_for_synthesis(result),
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars] + "\n... [结果过长已截断]"
    return text


async def stream_synthesis_llm_chunks(
    runner: Any,
    synthesis_messages: List[Any],
    state: SynthesisStreamState,
    *,
    start_title: str,
    complete_title: str,
    error_title: str,
    fallback: str,
    dedupe_warning_context: str = "",
) -> AsyncGenerator[Dict[str, Any], None]:
    state.final_llm = await AgentConfigProvider.get_synthesis_llm(
        streaming=True,
        config=runner.config,
    )
    try:
        async for chunk in state.final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
            state.last_chunk = chunk
            content = str(getattr(chunk, "content", "") or "")
            if not content:
                continue
            if not state.content_emitted:
                state.generation_start = time.time()
                state.content_emitted = True
                yield {
                    "type": "log",
                    "id": state.gen_log_id,
                    "title": start_title,
                    "status": "pending",
                    "started_at": int(state.generation_start * 1000),
                }
            state.full_content += content
            yield {"content": content}
        if state.generation_start:
            yield {
                "type": "log",
                "id": state.gen_log_id,
                "title": complete_title,
                "status": "success",
                "execution_time_ms": (time.time() - state.generation_start) * 1000,
            }
    except Exception as syn_err:
        logger.error("%s: %s", dedupe_warning_context or "Synthesis", syn_err)
        state.full_content = fallback
        yield {
            "type": "log",
            "id": f"syn_err_{uuid.uuid4().hex[:6]}",
            "title": error_title,
            "details": str(syn_err),
            "status": "error",
        }
        yield {"content": fallback}

    deduped = finalize_visible_reply(state.full_content)
    if deduped != state.full_content:
        if dedupe_warning_context:
            logger.warning(
                "%s (len %s -> %s)",
                dedupe_warning_context,
                len(state.full_content),
                len(deduped),
            )
        state.full_content = deduped
        if state.content_emitted:
            yield {"type": "retraction", "content": deduped}


def synthesis_token_usage(state: SynthesisStreamState) -> Dict[str, int]:
    return extract_tokens_from_message(state.last_chunk)
