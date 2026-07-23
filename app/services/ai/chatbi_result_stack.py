"""Versioned structured-result stack for ChatBI follow-ups and drill-down."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Any


@dataclass
class ChatBIAnalysisContext:
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    time_range: dict[str, Any] = field(default_factory=dict)
    time_grain: str = ""
    analysis_method: str = "overview"


@dataclass
class ChatBIResultRef:
    result_id: str = field(default_factory=lambda: f"result_{uuid.uuid4().hex}")
    parent_result_id: str | None = None
    question: str = ""
    dataset_name: str = ""
    data_source: str = ""
    sql: str = ""
    rows: Any = None
    result_summary: dict[str, Any] = field(default_factory=dict)
    analysis_context: ChatBIAnalysisContext = field(default_factory=ChatBIAnalysisContext)
    trace_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    observed_at: str | None = None
    data_as_of: str | None = None
    freshness: str = "dynamic"
    source_ref: str | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ChatBIResultRef":
        data = dict(payload or {})
        if not data.get("created_at") and data.get("saved_at"):
            data["created_at"] = data.get("saved_at")
        context = data.get("analysis_context")
        if not isinstance(context, ChatBIAnalysisContext):
            context_data = dict(context or {}) if isinstance(context, dict) else {}
            if not context_data.get("filters") and isinstance(data.get("filters"), list):
                context_data["filters"] = data.get("filters")
            allowed_context = {item.name for item in fields(ChatBIAnalysisContext)}
            data["analysis_context"] = ChatBIAnalysisContext(**{
                key: value for key, value in context_data.items() if key in allowed_context
            })
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass(frozen=True)
class ResultReferenceResolution:
    result: ChatBIResultRef | None
    candidates: list[ChatBIResultRef] = field(default_factory=list)


def push_result_ref(
    stack: list[ChatBIResultRef],
    result: ChatBIResultRef,
    *,
    max_depth: int = 10,
) -> list[ChatBIResultRef]:
    deduped = [item for item in stack if item.result_id != result.result_id]
    deduped.append(result)
    return deduped[-max(1, int(max_depth)) :]


_CURRENT_REFS = ("当前结果", "这个结果", "该结果", "刚才结果", "最新结果")
_PREVIOUS_REFS = ("上一个结果", "前一个结果", "上一张表", "上一步")


def _descriptive_term(reference: str) -> str:
    value = str(reference or "").strip()
    for filler in (
        "那张表", "这张表", "那个结果", "这个结果", "该结果", "结果",
        "刚才", "之前", "上面", "关于", "分析", "数据", "的",
    ):
        value = value.replace(filler, "")
    return re.sub(r"[\s，,。？！?：:]+", "", value)


def resolve_result_reference(
    stack: list[ChatBIResultRef],
    reference: str,
) -> ResultReferenceResolution:
    if not stack:
        return ResultReferenceResolution(None, [])
    text = str(reference or "").strip()
    exact = next((item for item in reversed(stack) if item.result_id == text), None)
    if exact is not None:
        return ResultReferenceResolution(exact, [])
    explicit = re.search(r"(?:result:)?(result_[0-9a-f]+|[A-Za-z][\w-]*)", text)
    if text.startswith("result:") and explicit:
        result_id = explicit.group(1)
        found = next((item for item in reversed(stack) if item.result_id == result_id), None)
        return ResultReferenceResolution(found, [] if found else list(reversed(stack)))
    if any(token in text for token in _PREVIOUS_REFS):
        return ResultReferenceResolution(stack[-2] if len(stack) > 1 else None, [])
    if not text or any(token in text for token in _CURRENT_REFS):
        return ResultReferenceResolution(stack[-1], [])

    term = _descriptive_term(text)
    if not term:
        return ResultReferenceResolution(stack[-1], [])
    matches = [item for item in reversed(stack) if term in _descriptive_term(item.question)]
    if len(matches) == 1:
        return ResultReferenceResolution(matches[0], [])
    if len(matches) > 1:
        return ResultReferenceResolution(None, matches)
    return ResultReferenceResolution(None, [])
