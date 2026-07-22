"""通用智能体会话级工具结果快照（供下一轮追问复用，独立于 ChatBI last_data_result）。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from app.services.ai.grounding.ledger import _is_non_empty_success_result
from app.services.ai.intent_service import (
    looks_like_context_action,
    looks_like_pure_result_followup,
    looks_like_strong_business_data_request,
)

logger = logging.getLogger(__name__)

SESSION_ARTIFACT_BLOCK_MARKER = "[上一轮可复用工具结果]"
MAX_TEXT_EXCERPT = 12_000
MAX_STRUCTURED_JSON_CHARS = 8_000
_MIN_TEXT_LEN_TO_SAVE = 80

# 不参与快照：时钟、检索碎片、ChatBI 专用、纯编排、知识库（有独立短路）
_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "get_current_time",
        "resolve_relative_dates",
        "memory_search",
        "fetch_user_long_term_memory",
        "search_knowledge_base",
        "get_dataset_schema",
        "execute_sql_query",
        "get_my_tasks",
        "list_process",
        "list_available_skills",
        "read_skill_instruction",
        "jira_get_projects",
        "create_skills",
        "update_user_preference",
        "delete_user_preference",
    }
)

_SENSITIVE_ARG_KEYS = frozenset(
    {"password", "token", "secret", "api_key", "apikey", "authorization", "cookie"}
)

_FRESH_DATA_PATTERN = re.compile(
    r"(重新查|再查|重查|再拉|重新拉|刷新数据|最新数据|实时数据|pull\s+again|refresh\s+data|re-?query)",
    re.I,
)

_WEAK_CONTEXT_REF = re.compile(
    r"(这个|这些|这份|上面|上述|刚才|刚刚|之前|上一|前述|同样|继续|that|this|above|previous)",
    re.I,
)


def _normalize_tool_output(tool_output: Any) -> tuple[str, Any]:
    """返回 (text_excerpt_source, structured_or_none)。"""
    if isinstance(tool_output, dict) and "data_blocks" in tool_output:
        text = str(tool_output.get("text") or "")
        blocks = tool_output.get("data_blocks")
        structured = {"data_blocks": blocks} if blocks else None
        return text, structured
    if isinstance(tool_output, (dict, list)):
        try:
            raw = json.dumps(tool_output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            raw = str(tool_output)
        return raw, tool_output if isinstance(tool_output, dict) else {"items": tool_output}
    text = str(tool_output or "")
    structured = None
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            structured = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return text, structured


def _truncate_text(text: str, limit: int = MAX_TEXT_EXCERPT) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 24] + "\n... [内容已截断]"


def _truncate_structured(value: Any) -> Any:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"preview": _truncate_text(str(value), 2000)}
    if len(raw) <= MAX_STRUCTURED_JSON_CHARS:
        return value
    return {"preview": raw[: MAX_STRUCTURED_JSON_CHARS - 24] + "... [JSON 已截断]"}


def _args_digest(tool_args: Mapping[str, Any] | None) -> str:
    safe: Dict[str, Any] = {}
    for key, val in (tool_args or {}).items():
        key_l = str(key).lower()
        if key_l in _SENSITIVE_ARG_KEYS or "secret" in key_l or "password" in key_l:
            safe[str(key)] = "[redacted]"
        else:
            safe[str(key)] = val
    try:
        blob = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(safe)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def artifact_candidate_score(
    *,
    tool_name: str,
    source_type: str,
    permission_scope: str,
    text: str,
    structured: Any,
) -> int:
    if tool_name in _EXCLUDED_TOOL_NAMES:
        return 0
    if permission_scope != "read":
        if tool_name != "sub_agent_call":
            return 0
    if not _is_non_empty_success_result(text if text.strip() else structured):
        return 0
    if len(text.strip()) < _MIN_TEXT_LEN_TO_SAVE:
        if not structured or not _is_non_empty_success_result(structured):
            return 0

    base = {
        "mcp": 50,
        "generic_api": 45,
        "class": 40,
        "system": 35,
        "static": 30,
    }.get(source_type, 25)
    if tool_name == "sub_agent_call":
        base = max(base, 32 if permission_scope == "read" else 28)
    if tool_name in {"system_http_request", "fetch_static_web_url", "web_search_baidu"}:
        base = max(base, 42)
    if tool_name in {"read_file", "excel_document_read", "word_document_read"}:
        base = max(base, 36)

    size_bonus = min(len(text), 20_000) // 400
    if isinstance(structured, (dict, list)) and structured:
        size_bonus += 5
    return base + size_bonus


def build_artifact_payload(
    *,
    tool_name: str,
    tool_args: Mapping[str, Any] | None,
    tool_output: Any,
    source_type: str,
    user_question: str,
    trace_id: str | None,
) -> Dict[str, Any]:
    text, structured = _normalize_tool_output(tool_output)
    return {
        "kind": source_type if source_type in {"mcp", "generic_api", "system"} else "tool",
        "tool_name": tool_name,
        "source_type": source_type,
        "tool_args_digest": _args_digest(tool_args),
        "user_question": str(user_question or "").strip()[:500],
        "text_excerpt": _truncate_text(text),
        "structured": _truncate_structured(structured) if structured is not None else None,
        "saved_at": datetime.now().isoformat(),
        "trace_id": str(trace_id or ""),
    }


def consider_turn_artifact_candidate(
    turn_state: Dict[str, Any] | None,
    *,
    tool_name: str,
    tool_args: Mapping[str, Any] | None,
    tool_output: Any,
    source_type: str = "static",
    permission_scope: str = "ask",
) -> None:
    """单轮内保留得分最高的工具结果（内存），轮末再写入 Redis。"""
    if not turn_state:
        return
    text, structured = _normalize_tool_output(tool_output)
    score = artifact_candidate_score(
        tool_name=tool_name,
        source_type=source_type,
        permission_scope=permission_scope,
        text=text,
        structured=structured,
    )
    if score <= 0:
        return
    payload = build_artifact_payload(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_output=tool_output,
        source_type=source_type,
        user_question=str(turn_state.get("user_question") or ""),
        trace_id=str(turn_state.get("trace_id") or ""),
    )
    payload["_score"] = score
    best = turn_state.get("best")
    if not best or int(best.get("_score") or 0) < score:
        turn_state["best"] = payload


def should_inject_session_artifact(user_question: str, artifact: Dict[str, Any] | None) -> bool:
    if not artifact or not str(artifact.get("text_excerpt") or "").strip():
        structured = artifact.get("structured")
        if not structured:
            return False
    q = str(user_question or "").strip()
    if not q:
        return False
    if _FRESH_DATA_PATTERN.search(q):
        return False
    if looks_like_pure_result_followup(q) or looks_like_context_action(q):
        return True
    if _WEAK_CONTEXT_REF.search(q) and not looks_like_strong_business_data_request(q):
        return True
    return False


def build_session_artifact_prompt_block(artifact: Dict[str, Any]) -> str:
    tool_name = str(artifact.get("tool_name") or "tool")
    saved_at = str(artifact.get("saved_at") or "")
    prior_q = str(artifact.get("user_question") or "")
    excerpt = str(artifact.get("text_excerpt") or "")
    structured = artifact.get("structured")

    lines = [
        SESSION_ARTIFACT_BLOCK_MARKER,
        f"- 来源工具：{tool_name}",
    ]
    if saved_at:
        lines.append(f"- 快照时间：{saved_at}")
    if prior_q:
        lines.append(f"- 触发该结果的用户问题：{prior_q}")
    lines.append("")
    lines.append("【结果摘录】")
    lines.append(excerpt or "（无文本摘录）")
    if structured is not None:
        try:
            struct_text = json.dumps(structured, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            struct_text = str(structured)
        if len(struct_text) > 4000:
            struct_text = struct_text[:4000] + "... [结构化部分已截断]"
        lines.append("")
        lines.append("【结构化片段】")
        lines.append(struct_text)
    lines.append("")
    lines.append("【复用规则】")
    lines.append(
        "1. 本轮若用户是在分析/总结/改写/导出/可视化「上一轮工具结果」，优先直接基于以上快照作答。"
    )
    lines.append(
        "2. 除非用户明确要求「重新查询/最新/刷新」，否则不要对同一工具重复发起相同查询。"
    )
    lines.append("3. 快照可能已截断；若信息不足，说明缺口并询问是否重新调用工具。")
    return "\n".join(lines)


def append_session_tool_artifact_to_system_prompt(
    system_content: str,
    user_question: str | None,
    artifact: Dict[str, Any] | None,
) -> str:
    base = str(system_content or "")
    if not should_inject_session_artifact(str(user_question or ""), artifact):
        return base
    if SESSION_ARTIFACT_BLOCK_MARKER in base:
        return base
    block = build_session_artifact_prompt_block(artifact or {})
    return f"{block}\n\n{base}"


async def load_session_tool_artifact(
    user_id: str | int | None,
    conversation_id: str | None,
) -> Dict[str, Any] | None:
    if not user_id or not conversation_id:
        return None
    try:
        from app.services.ai.memory_service import memory_service

        return await memory_service.get_session_tool_artifact(str(user_id), conversation_id)
    except Exception as exc:
        logger.warning("[SessionToolArtifact] load failed: %s", exc)
        return None


async def persist_turn_artifact_candidate(
    *,
    user_id: str | int | None,
    conversation_id: str | None,
    turn_state: Dict[str, Any] | None,
) -> None:
    if not user_id or not conversation_id or not turn_state:
        return
    best = turn_state.get("best")
    if not isinstance(best, dict):
        return
    payload = {k: v for k, v in best.items() if k != "_score"}
    try:
        from app.services.ai.memory_service import memory_service

        await memory_service.set_session_tool_artifact(str(user_id), conversation_id, payload)
    except Exception as exc:
        logger.warning("[SessionToolArtifact] persist failed: %s", exc)
