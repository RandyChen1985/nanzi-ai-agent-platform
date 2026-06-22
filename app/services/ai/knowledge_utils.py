"""知识库问答共用工具：dataset 解析、检索参数、引用校验。"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.core.context import get_current_agent_context

# 32 位 hex，与 RAGFlow dataset id 常见格式一致
_DATASET_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")
_DATASET_HINT_RE = re.compile(r"dataset_id[：:]\s*([a-fA-F0-9]{32})", re.I)
_CITATION_MARKER_RE = re.compile(r"\[ID:(\d+)\]")
# 兼容旧式 [1] 引用；负向前瞻避免把 [ID:1] 误判为 [1]
_LEGACY_CITATION_MARKER_RE = re.compile(r"\[(?!ID:)\d+\]")


def normalize_dataset_ids(raw: Union[str, List[Any], None]) -> List[str]:
    """
    将工具参数 / 配置中的 dataset_ids 规范为纯 ID 列表。
    兼容：逗号分隔、JSON 数组、Python 单引号列表、多余引号与方括号。
    """
    if raw is None:
        return []

    items: List[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            parsed: Any = None
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            items = parsed if isinstance(parsed, list) else [text]
        else:
            items = text.split(",")
    else:
        items = [raw]

    result: List[str] = []
    for item in items:
        token = str(item).strip().strip("[]\"' \t")
        if not token:
            continue
        if _DATASET_ID_RE.match(token):
            result.append(token)
            continue
        for match in _DATASET_ID_RE.findall(token):
            if match not in result:
                result.append(match)
    return result


def extract_dataset_ids_from_message(text: str) -> List[str]:
    """从用户消息（含 EmbedChat 附件注入行）解析 dataset ID。"""
    if not text:
        return []

    found: List[str] = []
    for match in _DATASET_HINT_RE.finditer(text):
        token = match.group(1)
        if token not in found:
            found.append(token)

    for match in re.finditer(r"\[['\"]?([a-fA-F0-9]{32})['\"]?\]", text):
        token = match.group(1)
        if token not in found:
            found.append(token)

    if not found:
        found = normalize_dataset_ids(text)
    return found


def merge_dataset_id_sources(*sources: Any) -> List[str]:
    """合并多来源 dataset_ids 并去重。"""
    merged: List[str] = []
    for source in sources:
        if not source:
            continue
        for item in normalize_dataset_ids(source):
            if item not in merged:
                merged.append(item)
    return merged


def format_dataset_ids_for_tool(dataset_ids: List[str]) -> Optional[str]:
    """将 ID 列表格式化为 search_knowledge_base 工具参数。"""
    if not dataset_ids:
        return None
    if len(dataset_ids) == 1:
        return dataset_ids[0]
    return str(dataset_ids).replace('"', "'")


def parse_knowledge_tool_payload(tool_output: Any) -> Dict[str, Any]:
    """解析 search_knowledge_base 返回的 JSON 载荷。"""
    if isinstance(tool_output, dict):
        return tool_output
    if not isinstance(tool_output, str):
        return {}
    text = tool_output.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def format_knowledge_tool_log_display(
    tool_output: Any,
    *,
    max_len: int = 1200,
) -> str:
    """将 search_knowledge_base 结果格式化为可读日志文本后再截断。"""
    from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_context

    payload = parse_knowledge_tool_payload(tool_output)
    if not payload:
        return truncate_for_context(str(tool_output or ""), max_len=max_len)

    lines: List[str] = []
    status = payload.get("status")
    if status == "empty":
        lines.append(str(payload.get("content") or "未找到与用户问题高度相关的文档片段。"))
        return truncate_for_context("\n".join(lines), max_len=max_len)

    content = str(payload.get("content") or "").strip()
    if content:
        lines.append("【检索摘要（供模型阅读）】")
        lines.append(content)

    citations = payload.get("citations")
    if isinstance(citations, list) and citations:
        lines.append("")
        lines.append(f"【引用片段】共 {len(citations)} 条")
        for idx, chunk in enumerate(citations, start=1):
            if not isinstance(chunk, dict):
                continue
            ref_id = str(chunk.get("id") or idx)
            doc_name = (
                chunk.get("doc_name")
                or chunk.get("document_name")
                or "Unknown Document"
            )
            similarity = chunk.get("similarity")
            sim_suffix = ""
            if isinstance(similarity, (int, float)):
                sim_suffix = f" · {float(similarity) * 100:.0f}%"
            body = str(chunk.get("content") or "").strip()
            lines.append(f"--- [ID:{ref_id}] {doc_name}{sim_suffix} ---")
            if body:
                lines.append(body)

    if not lines:
        return truncate_for_context(str(tool_output or ""), max_len=max_len)
    return truncate_for_context("\n".join(lines), max_len=max_len)


def knowledge_prefetch_had_citations(tool_output: Any) -> bool:
    """预检索是否返回了可引用的文档片段。"""
    payload = parse_knowledge_tool_payload(tool_output)
    citations = payload.get("citations")
    return isinstance(citations, list) and len(citations) > 0


def collect_citation_ids_from_payload(tool_output: Any) -> Set[str]:
    """从检索结果收集合法 [ID:n] 序号集合。"""
    payload = parse_knowledge_tool_payload(tool_output)
    citations = payload.get("citations")
    if not isinstance(citations, list):
        return set()
    ids: Set[str] = set()
    for idx, item in enumerate(citations, start=1):
        if isinstance(item, dict):
            ref = str(item.get("id") or idx)
            ids.add(ref)
    return ids


def filter_invalid_citation_markers(text: str, valid_ids: Set[str]) -> str:
    """移除回答中不存在于检索结果的 [ID:n] 标记。"""
    if not text or not valid_ids:
        return text

    def _replace(match: re.Match[str]) -> str:
        ref_id = match.group(1)
        return match.group(0) if ref_id in valid_ids else ""

    return _CITATION_MARKER_RE.sub(_replace, text)


def has_knowledge_citation_markers(text: str) -> bool:
    """回答中是否包含知识库引用标记（规范 [ID:n] 或旧式 [n]）。"""
    raw = str(text or "")
    if not raw.strip():
        return False
    if _CITATION_MARKER_RE.search(raw):
        return True
    return _LEGACY_CITATION_MARKER_RE.search(raw) is not None


def text_has_valid_citation_markers(text: str, valid_ids: Set[str]) -> bool:
    """回答中是否包含至少一个存在于检索结果中的引用序号。"""
    if not text or not valid_ids:
        return False
    for match in _CITATION_MARKER_RE.finditer(text):
        if match.group(1) in valid_ids:
            return True
    for match in _LEGACY_CITATION_MARKER_RE.finditer(text):
        legacy_match = re.search(r"\d+", match.group(0))
        if legacy_match and legacy_match.group(0) in valid_ids:
            return True
    return False


def _coerce_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def resolve_rag_retrieval_params(
    *,
    system_threshold: Any = None,
    system_weight: Any = None,
    system_top_k: Any = None,
) -> Tuple[float, float, int]:
    """
    解析检索参数优先级：Agent engine_config / rag_params > 系统配置 > 默认值。
    """
    threshold = _coerce_float(system_threshold, 0.2)
    vector_weight = _coerce_float(system_weight, 0.3)
    top_k = _coerce_int(system_top_k, 5)

    ctx = get_current_agent_context()
    if not ctx:
        return threshold, vector_weight, top_k

    rag_params = ctx.rag_params or {}
    engine_config = ctx.engine_config or {}

    agent_threshold = (
        rag_params.get("similarity_threshold")
        or rag_params.get("threshold")
        or engine_config.get("ragflow_similarity_threshold")
        or engine_config.get("similarity_threshold")
    )
    agent_weight = (
        rag_params.get("vector_similarity_weight")
        or rag_params.get("vector_weight")
        or engine_config.get("ragflow_vector_weight")
        or engine_config.get("vector_weight")
    )
    agent_top_k = (
        rag_params.get("top_k")
        or engine_config.get("top_k")
        or engine_config.get("ragflow_top_k")
    )

    threshold = _coerce_float(agent_threshold, threshold)
    vector_weight = _coerce_float(agent_weight, vector_weight)
    top_k = _coerce_int(agent_top_k, top_k)
    return threshold, vector_weight, top_k


NO_KNOWLEDGE_DATASET_MESSAGE = (
    "⚠️ 未指定可检索的知识库，本次知识库问答已终止。\n"
    "请在输入框选择知识库，或由管理员为当前智能体绑定 dataset_ids 后重试。"
)


def collect_knowledge_dataset_ids_from_messages(messages: List[Dict[str, Any]]) -> List[str]:
    """从会话内全部 user 消息的 knowledge_base 附件提取 dataset ID（支持多轮追问继承）。"""
    if not messages:
        return []
    ids: List[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        for file_obj in message.get("files") or []:
            if file_obj.get("type") != "knowledge_base":
                continue
            url = str(file_obj.get("url") or "").strip()
            if url and url not in ids:
                ids.append(url)
    return normalize_dataset_ids(ids)


def merge_request_knowledge_dataset_ids(
    request_ids: Optional[List[str]],
    messages: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """合并 API 结构化字段与消息附件中的知识库 ID。"""
    return merge_dataset_id_sources(
        request_ids,
        collect_knowledge_dataset_ids_from_messages(messages or []),
    )


def resolve_bound_dataset_ids(
    *,
    explicit_tool_ids: Any = None,
    query: str = "",
) -> List[str]:
    """合并工具参数、请求级/智能体 dataset、消息提示中的 ID（不含系统默认）。"""
    ctx = get_current_agent_context()
    return merge_dataset_id_sources(
        explicit_tool_ids,
        ctx.knowledge_dataset_ids if ctx else None,
        ctx.dataset_ids if ctx else None,
        extract_dataset_ids_from_message(query),
    )


async def resolve_knowledge_dataset_ids(
    *,
    explicit_tool_ids: Any = None,
    query: str = "",
) -> Tuple[List[str], Optional[str]]:
    """
    解析知识库检索范围。

    Returns:
        (dataset_ids, error_message)。error_message 非空表示应阻断检索。
    """
    from app.services.config_service import ConfigService

    bound_ids = resolve_bound_dataset_ids(
        explicit_tool_ids=explicit_tool_ids,
        query=query,
    )
    if bound_ids:
        return bound_ids, None

    ctx = get_current_agent_context()
    if ctx and ctx.require_explicit_dataset:
        return [], NO_KNOWLEDGE_DATASET_MESSAGE

    default_ids_str = await ConfigService.get("knowledge_ragflow_dataset_ids")
    fallback_ids = normalize_dataset_ids(default_ids_str) if default_ids_str else []
    if fallback_ids:
        return fallback_ids, None

    return [], (
        "[System Warning] No knowledge base datasets configured. "
        "Please contact admin to set 'knowledge_ragflow_dataset_ids'."
    )


async def build_rag_retrieval_debug_meta() -> Dict[str, Any]:
    """构建 Debug / meta 事件展示用的 RAG 实参快照。"""
    from app.services.config_service import ConfigService

    ctx = get_current_agent_context()
    sys_threshold = await ConfigService.get("knowledge_ragflow_similarity_threshold")
    sys_weight = await ConfigService.get("knowledge_ragflow_vector_weight")
    sys_top_k = await ConfigService.get("knowledge_ragflow_metadata_top_k")
    threshold, vector_weight, top_k = resolve_rag_retrieval_params(
        system_threshold=sys_threshold,
        system_weight=sys_weight,
        system_top_k=sys_top_k,
    )
    bound_ids = resolve_bound_dataset_ids()
    return {
        "dataset_ids": bound_ids,
        "request_dataset_ids": list(ctx.knowledge_dataset_ids) if ctx else [],
        "agent_dataset_ids": list(ctx.dataset_ids) if ctx else [],
        "require_explicit_dataset": bool(ctx.require_explicit_dataset) if ctx else False,
        "similarity_threshold": threshold,
        "vector_similarity_weight": vector_weight,
        "top_k": top_k,
    }
