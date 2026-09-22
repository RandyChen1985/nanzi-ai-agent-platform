"""知识库问答共用工具：dataset 解析、检索参数、引用校验。"""
from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from app.core.context import get_current_agent_context

logger = logging.getLogger(__name__)

# RAGFlow 现网 dataset id 缓存（秒）：避免每轮检索都打 list API
_ALIVE_DATASET_IDS_CACHE_KEY = "knowledge:ragflow:alive_dataset_ids"
_ALIVE_DATASET_IDS_CACHE_TTL = 60

# 32 位 hex，与 RAGFlow dataset id 常见格式一致
_DATASET_ID_RE = re.compile(r"^[a-fA-F0-9]{32}$")
_DATASET_HINT_RE = re.compile(r"dataset_id[：:]\s*([a-fA-F0-9]{32})", re.I)
_CITATION_MARKER_RE = re.compile(r"\[ID:(\d+)\]")
# 兼容旧式 [1] 引用；负向前瞻避免把 [ID:1] 误判为 [1]
_LEGACY_CITATION_MARKER_RE = re.compile(r"\[(?!ID:)\d+\]")

#: 系统级默认知识库数据集配置键。智能体未显式绑定 dataset_ids 时，运行时回退到它
#: （见 resolve_knowledge_dataset_ids），因此就绪判定也必须认同一口径。
SYSTEM_DEFAULT_DATASET_IDS_CONFIG_KEY = "knowledge_ragflow_dataset_ids"


async def load_system_default_dataset_ids() -> List[str]:
    """读取系统级默认知识库数据集 id 列表（未配置/读取失败时返回空列表）。

    与 ``resolve_knowledge_dataset_ids`` 的兜底口径保持一致，供就绪判定等
    非检索场景复用；读取失败按"未配置"处理（保持严格，不放宽判定）。
    """
    from app.services.config_service import ConfigService

    try:
        raw = await ConfigService.get(SYSTEM_DEFAULT_DATASET_IDS_CONFIG_KEY)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[KnowledgeUtils] Load system default dataset ids failed: %s", exc)
        return []
    return normalize_dataset_ids(raw) if raw else []


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
    from app.services.ai.runtime.agentscope.stream_reconcile import truncate_for_display

    payload = parse_knowledge_tool_payload(tool_output)
    if not payload:
        return truncate_for_display(str(tool_output or ""), max_len=max_len)

    lines: List[str] = []
    status = payload.get("status")
    if status == "empty":
        lines.append(str(payload.get("content") or "未找到与用户问题高度相关的文档片段。"))
        return truncate_for_display("\n".join(lines), max_len=max_len)

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
        return truncate_for_display(str(tool_output or ""), max_len=max_len)
    return truncate_for_display("\n".join(lines), max_len=max_len)


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

    # 临时覆盖调试选项：读取 debug_context 中的自定义检索参数
    from app.core.context import get_debug_option
    dbg_threshold = get_debug_option("knowledge_ragflow_similarity_threshold")
    dbg_weight = get_debug_option("knowledge_ragflow_vector_weight")
    dbg_top_k = get_debug_option("knowledge_ragflow_metadata_top_k")

    if dbg_threshold is not None:
        threshold = _coerce_float(dbg_threshold, threshold)
    if dbg_weight is not None:
        vector_weight = _coerce_float(dbg_weight, vector_weight)
    if dbg_top_k is not None:
        top_k = _coerce_int(dbg_top_k, top_k)

    return threshold, vector_weight, top_k


NO_KNOWLEDGE_DATASET_MESSAGE = (
    "⚠️ 未指定可检索的知识库，本次知识库问答已终止。\n"
    "请在输入框选择知识库，或由管理员为当前智能体绑定 dataset_ids 后重试。"
)

KNOWLEDGE_BASE_DISABLED_TOOL_ERROR = (
    "[Tool Error] 知识库功能未开启。请联系管理员在系统配置 → 知识库设置中启用「知识库功能」。"
)

KNOWLEDGE_BASE_DISABLED_USER_MESSAGE = (
    "⚠️ 知识库功能未开启，本次知识库问答已终止。\n"
    "请联系管理员在系统配置 → 知识库设置中启用「知识库功能」。"
)


async def is_knowledge_base_enabled() -> bool:
    """系统是否启用知识库（RAGFlow 检索与管理）。"""
    from app.services.config_service import ConfigService

    val = await ConfigService.get("knowledge_base_enabled", default="true")
    return str(val).strip().lower() in {"true", "1", "yes", "on"}


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


def collect_current_turn_knowledge_dataset_ids_from_messages(
    messages: List[Dict[str, Any]],
) -> List[str]:
    """只提取最后一条 user 消息的知识库附件，作为本轮显式检索证据。"""
    if not messages:
        return []
    for message in reversed(messages):
        if message.get("role") == "user":
            return collect_knowledge_dataset_ids_from_messages([message])
    return []


def has_knowledge_context_in_messages(messages: List[Dict[str, Any]]) -> bool:
    """判断历史是否包含可复用的知识库上下文，而不是把它当成本轮检索指令。"""
    if collect_knowledge_dataset_ids_from_messages(messages):
        return True
    for message in messages or []:
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "")
        if _CITATION_MARKER_RE.search(content):
            return True
    return False


def merge_request_knowledge_dataset_ids(
    request_ids: Optional[List[str]],
    messages: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """解析用户知识库范围：显式请求优先并严格覆盖历史附件。"""
    explicit_ids = merge_dataset_id_sources(request_ids)
    if explicit_ids:
        return explicit_ids
    return merge_dataset_id_sources(
        collect_knowledge_dataset_ids_from_messages(messages or []),
    )


def resolve_bound_dataset_ids(
    *,
    explicit_tool_ids: Any = None,
    query: str = "",
) -> List[str]:
    """按显式工具参数、用户范围、智能体默认范围、用户权限范围解析 ID。"""
    ctx = get_current_agent_context()
    explicit_ids = normalize_dataset_ids(explicit_tool_ids)
    if explicit_ids:
        return explicit_ids
    if ctx and ctx.knowledge_dataset_ids:
        return list(ctx.knowledge_dataset_ids)
    if ctx and ctx.dataset_ids:
        return list(ctx.dataset_ids)
    if ctx and ctx.agent_dataset_ids:
        return list(ctx.agent_dataset_ids)
    return merge_dataset_id_sources(
        ctx.dataset_ids if ctx else None,
        extract_dataset_ids_from_message(query),
    )


async def _load_ragflow_alive_dataset_ids() -> Optional[Set[str]]:
    """
    读取 RAGFlow 现网存在的 dataset id 集合。
    返回 None 表示未能获取（应做 fail-open，避免检索整条链路被堵死）。
    """
    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is not None:
            cached = await redis.get(_ALIVE_DATASET_IDS_CACHE_KEY)
            if cached:
                try:
                    data = json.loads(cached)
                    if isinstance(data, list):
                        return {str(x) for x in data if x}
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
    except Exception as exc:
        logger.debug("[KnowledgeUtils] alive dataset cache read skipped: %s", exc)

    try:
        from app.services.ai.ragflow_client import RagFlowClient

        client = RagFlowClient(config_prefix="knowledge_ragflow")
        items = await client.list_datasets(page=1, page_size=500)
        alive = {
            str(item.get("id") or "").strip()
            for item in (items or [])
            if item.get("id")
        }
    except Exception as exc:
        logger.warning(
            "[KnowledgeUtils] Failed to load RAGFlow alive dataset ids: %s",
            exc,
        )
        return None

    try:
        from app.core.redis import get_redis

        redis = await get_redis()
        if redis is not None:
            await redis.set(
                _ALIVE_DATASET_IDS_CACHE_KEY,
                json.dumps(sorted(alive)),
                ex=_ALIVE_DATASET_IDS_CACHE_TTL,
            )
    except Exception as exc:
        logger.debug("[KnowledgeUtils] alive dataset cache write skipped: %s", exc)

    return alive


async def filter_alive_knowledge_dataset_ids(dataset_ids: List[str]) -> List[str]:
    """从待检索 ID 中剔除 RAGFlow 侧已不存在（失联）的知识库。"""
    if not dataset_ids:
        return []

    # 保序去重，避免同一 ID 重复打到 RAGFlow
    unique_ids: List[str] = []
    seen: Set[str] = set()
    for dataset_id in dataset_ids:
        if not dataset_id or dataset_id in seen:
            continue
        seen.add(dataset_id)
        unique_ids.append(dataset_id)

    alive = await _load_ragflow_alive_dataset_ids()
    if alive is None:
        return unique_ids

    kept: List[str] = []
    dropped: List[str] = []
    for dataset_id in unique_ids:
        if dataset_id in alive:
            kept.append(dataset_id)
        else:
            dropped.append(dataset_id)

    if dropped:
        logger.warning(
            "[KnowledgeUtils] Dropped missing RAGFlow dataset ids before retrieve: %s",
            dropped,
        )
    return kept


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
    bound_ids = resolve_bound_dataset_ids(
        explicit_tool_ids=explicit_tool_ids,
        query=query,
    )
    if bound_ids:
        alive_ids = await filter_alive_knowledge_dataset_ids(bound_ids)
        if not alive_ids:
            return [], (
                "⚠️ 所选知识库在 RAGFlow 侧已失联或不存在，本次知识库问答已终止。\n"
                "请重新选择可用知识库，或联系管理员清理失效授权后重试。"
            )
        return alive_ids, None

    ctx = get_current_agent_context()
    if ctx and ctx.require_explicit_dataset:
        return [], NO_KNOWLEDGE_DATASET_MESSAGE

    fallback_ids = await load_system_default_dataset_ids()
    if fallback_ids:
        alive_ids = await filter_alive_knowledge_dataset_ids(fallback_ids)
        if not alive_ids:
            return [], (
                "[System Warning] Configured knowledge_ragflow_dataset_ids are missing in RAGFlow."
            )
        return alive_ids, None

    return [], (
        "[System Warning] No knowledge base datasets configured. "
        "Please contact admin to set 'knowledge_ragflow_dataset_ids'."
    )


async def build_rag_retrieval_debug_meta() -> Dict[str, Any]:
    """构建 Debug / meta 事件展示用的 RAG 实参快照。"""
    import asyncio

    from app.services.config_service import ConfigService

    ctx = get_current_agent_context()
    sys_threshold, sys_weight, sys_top_k = await asyncio.gather(
        ConfigService.get("knowledge_ragflow_similarity_threshold"),
        ConfigService.get("knowledge_ragflow_vector_weight"),
        ConfigService.get("knowledge_ragflow_metadata_top_k"),
    )
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
