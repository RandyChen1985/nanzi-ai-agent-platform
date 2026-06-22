"""Structured ChatBI query intent helpers.

This module keeps the semantic intent layer independent from the runner so it
can fail open: if LLM analysis or JSON parsing fails, existing keyword-only
schema search still works.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SemanticIntentFilter:
    phrase: str
    semantic_type: str = ""
    expected_column_types: list[str] = field(default_factory=list)
    avoid_column_types: list[str] = field(default_factory=list)
    relation: str = ""


@dataclass
class DataQuerySemanticIntent:
    goal: str = ""
    keywords: str = ""
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[SemanticIntentFilter] = field(default_factory=list)
    time_range: str = ""
    grain: str = ""
    reasoning: str = ""

    def has_content(self) -> bool:
        return bool(
            self.goal
            or self.keywords
            or self.metrics
            or self.dimensions
            or self.filters
            or self.time_range
            or self.grain
        )


def _as_clean_str(value: Any, *, max_len: int = 80) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def _as_str_list(value: Any, *, max_items: int = 8, max_len: int = 40) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = re.split(r"[,，、\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _as_clean_str(raw, max_len=max_len)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _extract_json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def parse_semantic_intent_payload(content: str, *, fallback_question: str = "") -> DataQuerySemanticIntent:
    data = _extract_json_object(content)
    filters: list[SemanticIntentFilter] = []
    raw_filters = data.get("filters") if isinstance(data, dict) else None
    if isinstance(raw_filters, list):
        for raw in raw_filters[:8]:
            if not isinstance(raw, dict):
                continue
            phrase = _as_clean_str(raw.get("phrase") or raw.get("value") or raw.get("condition"))
            if not phrase:
                continue
            filters.append(
                SemanticIntentFilter(
                    phrase=phrase,
                    semantic_type=_as_clean_str(raw.get("semantic_type") or raw.get("type")),
                    expected_column_types=_as_str_list(
                        raw.get("expected_column_types") or raw.get("preferred_columns") or raw.get("expected_columns")
                    ),
                    avoid_column_types=_as_str_list(
                        raw.get("avoid_column_types") or raw.get("avoid_columns") or raw.get("wrong_columns")
                    ),
                    relation=_as_clean_str(raw.get("relation")),
                )
            )
    keywords = _as_clean_str(data.get("keywords"), max_len=300)
    return DataQuerySemanticIntent(
        goal=_as_clean_str(data.get("goal") or fallback_question, max_len=200),
        keywords=keywords,
        metrics=_as_str_list(data.get("metrics")),
        dimensions=_as_str_list(data.get("dimensions")),
        filters=filters,
        time_range=_as_clean_str(data.get("time_range"), max_len=80),
        grain=_as_clean_str(data.get("grain"), max_len=80),
        reasoning=_as_clean_str(data.get("reasoning"), max_len=200),
    )


def derive_keywords_from_semantic_intent(intent: DataQuerySemanticIntent | None, *, max_terms: int = 12) -> str:
    if not intent or not intent.has_content():
        return ""
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = _as_clean_str(value, max_len=40)
        if not text or text in seen:
            return
        seen.add(text)
        terms.append(text)

    for metric in intent.metrics:
        add(metric)
    for dimension in intent.dimensions:
        add(dimension)
    for item in intent.filters:
        add(item.phrase)
        for expected in item.expected_column_types:
            add(expected)
    if intent.grain:
        add(intent.grain)
    if not terms and intent.goal:
        for token in re.split(r"[\s,，、]+", intent.goal):
            add(token)
    return " ".join(terms[:max_terms])


def semantic_intent_to_dict(intent: DataQuerySemanticIntent | None) -> dict[str, Any]:
    if not intent:
        return {}
    return asdict(intent)


def semantic_intent_from_dict(data: Any) -> DataQuerySemanticIntent | None:
    if not isinstance(data, dict):
        return None
    filters: list[SemanticIntentFilter] = []
    for item in data.get("filters") or []:
        if not isinstance(item, dict):
            continue
        phrase = _as_clean_str(item.get("phrase"))
        if not phrase:
            continue
        filters.append(
            SemanticIntentFilter(
                phrase=phrase,
                semantic_type=_as_clean_str(item.get("semantic_type")),
                expected_column_types=_as_str_list(item.get("expected_column_types")),
                avoid_column_types=_as_str_list(item.get("avoid_column_types")),
                relation=_as_clean_str(item.get("relation")),
            )
        )
    intent = DataQuerySemanticIntent(
        goal=_as_clean_str(data.get("goal"), max_len=200),
        keywords=_as_clean_str(data.get("keywords"), max_len=300),
        metrics=_as_str_list(data.get("metrics")),
        dimensions=_as_str_list(data.get("dimensions")),
        filters=filters,
        time_range=_as_clean_str(data.get("time_range"), max_len=80),
        grain=_as_clean_str(data.get("grain"), max_len=80),
        reasoning=_as_clean_str(data.get("reasoning"), max_len=200),
    )
    return intent if intent.has_content() else None


def build_semantic_intent_prompt(
    user_question: str,
    standalone_query: str,
    example_context: str,
    conversation_context: str = "",
) -> str:
    context_block = ""
    if str(conversation_context or "").strip():
        context_block = (
            f"\n\n【最近对话上下文】\n{conversation_context.strip()}\n\n"
            "【最新提问优先级】\n"
            "最近对话只用于补全省略、指代、延续的业务对象、指标、维度和筛选条件；"
            "若最近对话与【独立查数问题】或【用户原始问题】冲突，必须以最新提问新增/修改的条件为准。"
        )
    return (
        "你是 ChatBI 的结构化业务意图分析器。你的任务不是生成 SQL，而是在执行 get_dataset_schema 和 SQL 之前，"
        "把用户查数需求拆成可复核的业务意图帧。\n\n"
        "请识别：业务目标、指标、维度、筛选条件、时间范围、聚合粒度，以及每个筛选条件应优先绑定的字段语义。"
        "特别注意地点、组织、客户、系统、环境、等级、状态等口语词可能是父级范围、别名或业务分类，"
        "不要把父级范围词机械绑定到明细名称字段。\n\n"
        "只返回 JSON，不要 Markdown。格式如下：\n"
        '{"keywords":"用于 get_dataset_schema 的 3-10 个关键词",'
        '"goal":"用户业务目标","metrics":["指标"],"dimensions":["维度"],'
        '"filters":[{"phrase":"用户筛选原词","semantic_type":"geographic_region|organization|customer|status|category|entity|time|other",'
        '"expected_column_types":["应优先绑定的字段语义或常见物理名"],'
        '"avoid_column_types":["容易误绑的字段语义或物理名"],'
        '"relation":"exact_value|alias|parent_region_or_scope|category_scope|contains|unknown"}],'
        '"time_range":"时间范围或无","grain":"聚合粒度或明细粒度","reasoning":"一句话说明"}\n\n'
        "约束：\n"
        "1. keywords 必须适合检索数据集/表/字段/指标定义，不要输出完整问题原句。\n"
        "2. DataQueryIntentFrame 不是数据库 Schema，不得编造物理表名、物理字段名或 JOIN 键；"
        "SQL 的 FROM/JOIN/字段必须以 get_dataset_schema 返回为准。\n"
        "3. expected_column_types 只能写字段语义或候选字段名线索，例如 区域/gxqy/region/area；"
        "这些线索不是已确认物理字段名。\n"
        "4. avoid_column_types 用于指出容易误绑字段，例如 地域条件不要优先绑到机房名称/shipName。\n"
        "5. 若无法判断字段语义，relation 使用 unknown，但不要编造具体数据值。\n\n"
        f"【用户原始问题】\n{user_question}\n\n"
        f"【独立查数问题】\n{standalone_query}\n\n"
        f"【命中的历史案例线索】\n{example_context}"
        f"{context_block}"
    )


def format_semantic_intent_context(intent: DataQuerySemanticIntent | None) -> str:
    if not intent or not intent.has_content():
        return ""
    lines = ["【结构化业务意图（用于字段绑定自检，不是 SQL 结果）】"]
    if intent.goal:
        lines.append(f"- 业务目标: {intent.goal}")
    if intent.metrics:
        lines.append(f"- 指标: {'、'.join(intent.metrics)}")
    if intent.dimensions:
        lines.append(f"- 维度: {'、'.join(intent.dimensions)}")
    if intent.time_range:
        lines.append(f"- 时间范围: {intent.time_range}")
    if intent.grain:
        lines.append(f"- 粒度: {intent.grain}")
    for item in intent.filters:
        detail = f"- 筛选: `{item.phrase}`"
        if item.semantic_type:
            detail += f"；语义类型: {item.semantic_type}"
        if item.relation:
            detail += f"；关系: {item.relation}"
        if item.expected_column_types:
            detail += f"；优先绑定字段语义: {'、'.join(item.expected_column_types)}"
        if item.avoid_column_types:
            detail += f"；避免误绑: {'、'.join(item.avoid_column_types)}"
        lines.append(detail)
    lines.append(
        "注意：本意图帧不是已确认物理表名或字段名来源；"
        "expected_column_types / avoid_column_types 只是字段语义线索，SQL 的 FROM/JOIN/字段必须以 get_dataset_schema 返回为准。"
    )
    lines.append(
        "生成 SQL 前请逐项核对：每个用户筛选词是否绑定到了语义匹配的字段；"
        "若字段语义不匹配，应先重查 Schema 或更换字段，而不是直接把口语词写进任意名称字段。"
    )
    return "\n".join(lines)


def _preview_diagnostics(diagnostics: list[dict[str, Any]], *, max_items: int = 3) -> list[str]:
    lines: list[str] = []
    for item in diagnostics[:max_items]:
        column = _as_clean_str(item.get("column"))
        used_values = "、".join(_as_str_list(item.get("used_values"), max_items=4))
        candidates = "、".join(_as_str_list(item.get("candidates"), max_items=6))
        alternatives = "、".join(_as_str_list(item.get("alternative_columns"), max_items=6))
        parts = [f"字段 `{column}`" if column else "字段（未知）"]
        if used_values:
            parts.append(f"使用值: {used_values}")
        if candidates:
            parts.append(f"候选值: {candidates}")
        if alternatives:
            parts.append(f"备选字段: {alternatives}")
        lines.append("- " + "；".join(parts))
    return lines


def format_empty_result_semantic_repair_context(
    intent: DataQuerySemanticIntent | None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> str:
    if not intent or not intent.has_content():
        return ""
    lines = ["【空结果语义复核】请基于原始业务意图重新判断 WHERE 条件，而不是只做字符串候选值匹配。"]
    if intent.goal:
        lines.append(f"- 原始目标: {intent.goal}")
    for item in intent.filters:
        relation_note = ""
        if item.relation in {"parent_region_or_scope", "category_scope"}:
            relation_note = "；这是父级/范围条件，候选明细值可能不会直接包含用户原词"
        lines.append(
            f"- 用户筛选 `{item.phrase}`: 类型 {item.semantic_type or 'unknown'}，关系 {item.relation or 'unknown'}"
            f"{relation_note}"
        )
        if item.expected_column_types:
            lines.append(f"  优先核对字段语义: {'、'.join(item.expected_column_types)}")
        if item.avoid_column_types:
            lines.append(f"  避免误绑字段: {'、'.join(item.avoid_column_types)}")
    if diagnostics:
        lines.append("【本次空结果候选证据摘要】")
        lines.extend(_preview_diagnostics(diagnostics))
    lines.append(
        "修复要求：先判断用户筛选词是精确值、别名、父级/范围条件还是分类条件；"
        "不能仅因候选值不包含原词就判定无数据。若候选值像下级实体，请优先查找区域/组织/分类字段，"
        "或用诊断 SQL 证明父子关系后再修正最终 SQL。"
    )
    return "\n".join(lines)
