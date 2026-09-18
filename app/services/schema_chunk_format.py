"""
ChatBI 元数据 Schema 检索块格式化：统一工具输出、HTTP API 与后台 YAML 导出的分隔头。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 新格式：--- [Schema:1] type=table dataset=sales_detail table=dim_region score=0.86 ---
_SCHEMA_HEADER_RE = re.compile(
    r"---\s*\[Schema:(\d+)\]\s+"
    r"type=(\w+)"
    r"(?:\s+dataset=([^\s]+))?"
    r"(?:\s+table=([^\s]+))?"
    r"(?:\s+score=([0-9]+(?:\.[0-9]+)?))?"
    r"(?:\s+dataset_id=([^\s]+))?"
    r"\s*---",
    re.IGNORECASE,
)

# 旧格式（兼容）：[置信度: 0.86] + --- Source: xxx ---
_LEGACY_CONFIDENCE_RE = re.compile(r"置信度[:：]\s*([0-9]+(?:\.[0-9]+)?)")
_LEGACY_BLOCK_RE = re.compile(
    r"\[置信度[:：]\s*([0-9]+(?:\.[0-9]+)?)\]\s*"
    r"(?:\n|\r\n)---\s*Source:\s*([^\n\r]+?)\s*---",
    re.IGNORECASE,
)

_TABLE_NAME_RE = re.compile(r"^table_name:\s*(\S+)\s*$", re.MULTILINE)
_DATASET_RE = re.compile(r"^dataset:\s*(\S+)\s*$", re.MULTILINE)
_METRICS_SCOPE_RE = re.compile(r"^metrics_scope:\s*(.+?)\s*$", re.MULTILINE)


def infer_schema_chunk_meta(content: str, doc_name: str = "") -> Tuple[str, Optional[str], Optional[str]]:
    """
    从 YAML 正文或 doc_name 推断块类型与标识。
    Returns: (chunk_type, table_name, dataset_name)
    """
    text = str(content or "")
    doc = str(doc_name or "").strip().lower()

    if doc.endswith("_metrics.txt") or "metrics_scope:" in text:
        dataset = None
        m_scope = _METRICS_SCOPE_RE.search(text)
        if m_scope:
            dataset = m_scope.group(1).strip()
        m_ds = _DATASET_RE.search(text)
        if m_ds:
            dataset = m_ds.group(1).strip()
        return "metrics", None, dataset

    table_name = None
    m_table = _TABLE_NAME_RE.search(text)
    if m_table:
        table_name = m_table.group(1).strip()
    elif doc.endswith(".txt"):
        table_name = doc.rsplit(".", 1)[0].split(".")[-1] or None

    dataset = None
    m_ds = _DATASET_RE.search(text)
    if m_ds:
        dataset = m_ds.group(1).strip()
    elif doc and "." in doc:
        dataset = doc.rsplit(".", 1)[0]

    return "table", table_name, dataset


def format_schema_chunk(
    index: int,
    content: str,
    *,
    score: Optional[float] = None,
    doc_name: str = "",
    chunk_type: Optional[str] = None,
    table_name: Optional[str] = None,
    dataset: Optional[str] = None,
    dataset_id: Optional[Any] = None,
) -> str:
    """将单段 YAML 正文包装为带分隔头的 Schema 检索块。"""
    body = str(content or "").strip()
    if not body:
        return ""

    inferred_type, inferred_table, inferred_dataset = infer_schema_chunk_meta(body, doc_name)
    ctype = chunk_type or inferred_type
    tbl = table_name or inferred_table
    ds = dataset or inferred_dataset

    parts = [f"--- [Schema:{index}]", f"type={ctype}"]
    if ds:
        parts.append(f"dataset={ds}")
    if tbl:
        parts.append(f"table={tbl}")
    if score is not None:
        try:
            parts.append(f"score={float(score):.2f}")
        except (TypeError, ValueError):
            pass
    if dataset_id not in (None, ""):
        parts.append(f"dataset_id={str(dataset_id).strip()}")
    header = " ".join(parts) + " ---"
    return f"{header}\n{body}"


def format_schema_hits(hits: List[Dict[str, Any]]) -> str:
    """批量格式化检索命中（每项含 content、similarity、doc_name）。"""
    chunks: List[str] = []
    for idx, hit in enumerate(hits, start=1):
        content = str(hit.get("content") or "").strip()
        if not content:
            continue
        try:
            score = float(hit.get("similarity", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = None
        formatted = format_schema_chunk(
            idx,
            content,
            score=score,
            doc_name=str(hit.get("doc_name") or ""),
            dataset_id=(
                hit.get("metadata_dataset_id")
                or hit.get("dataset_id")
                or hit.get("rag_dataset_id")
            ),
        )
        if formatted:
            chunks.append(formatted)
    return "\n\n".join(chunks)


def count_schema_hits(text: Any) -> int:
    """统计 Schema 工具输出中的元数据块数量（兼容新旧分隔头）。"""
    raw = str(text or "")
    new_count = len(_SCHEMA_HEADER_RE.findall(raw))
    if new_count:
        return new_count
    return len(_LEGACY_BLOCK_RE.findall(raw))


def estimate_text_tokens(text: Any) -> int:
    """粗略估算文本 token 数（用于日志展示与上下文预算判定）。

    ⚠️ **口径必须与运行时一致**：这里刻意采用与 AgentScope ``count_tokens`` 相同的
    「UTF-8 字节数 ÷ 4」口径，而不是按字符类型加权。原因如下。

    平台的历史裁剪水位线（``window_for_context``）、上下文占用展示、以及 AgentScope
    内部自带的上下文压缩，**判定的是同一件事**——"这段上下文还能不能装下"。三者若用
    不同度量，就会出现互相打架的判定，且展示值与真实用量不可比。

    旧实现是 ``cjk * 1.5 + other / 4``，对中文高估约 2 倍（1 个汉字估算 1.5 token，
    实际约 0.6；而字节口径为 3/4 = 0.75）。后果是中文为主的会话在模型窗口**只用到约
    三分之一**时就被判定超预算并触发压缩——用户感受即"明明还有空间就压了"；反过来
    对英文是低估（0.25 < 实际约 0.3），又可能超窗。

    换成字节口径后，中文估算 0.75 token/字（较实际保守约 25%，偏安全），英文约
    0.25 token/字符（准确），且与运行时判定统一。
    """
    raw = str(text or "")
    if not raw:
        return 0
    return max(1, int(len(raw.encode("utf-8")) / 4 + 0.5))


def format_schema_hit_summary(text: Any) -> Optional[str]:
    """生成工具日志用的命中摘要行；无命中块时返回 None。"""
    hit_count = count_schema_hits(text)
    if hit_count <= 0:
        return None
    token_est = estimate_text_tokens(text)
    return f"[命中摘要] 共命中 {hit_count} 条元数据记录，占用约 {token_est} token"


def extract_schema_confidence_values(text: Any) -> List[float]:
    """从 Schema 工具输出中提取置信度/score（兼容新旧格式）。"""
    raw = str(text or "")
    values: List[float] = []

    for match in _SCHEMA_HEADER_RE.finditer(raw):
        score_text = match.group(5)
        if score_text:
            try:
                values.append(float(score_text))
            except ValueError:
                continue

    for value in _LEGACY_CONFIDENCE_RE.findall(raw):
        try:
            values.append(float(value))
        except ValueError:
            continue

    return values


def _candidate_key(chunk_type: str, dataset: Optional[str], table: Optional[str], source: str) -> str:
    ctype = (chunk_type or "table").lower()
    ds = (dataset or "").strip().lower()
    tbl = (table or "").strip().lower()
    if ctype == "metrics":
        return f"metrics:{ds or source.strip().lower()}"
    if ds and tbl:
        return f"{ds}.{tbl}"
    if tbl:
        return tbl
    return source.strip().lower()


def _new_schema_dataset_candidates(text: str) -> List[Dict[str, Any]]:
    """按数据集聚合新格式 Schema 块，避免同一数据集多表重复算候选。"""
    grouped: Dict[str, Dict[str, Any]] = {}
    for match in _SCHEMA_HEADER_RE.finditer(text):
        try:
            score = float(match.group(5) or 0)
        except ValueError:
            continue
        chunk_type = match.group(2) or "table"
        dataset = (match.group(3) or "").strip()
        table = (match.group(4) or "").strip()
        dataset_id = (match.group(6) or "").strip()
        source = f"schema_{match.group(1)}"
        identity = dataset_id or dataset.casefold() or _candidate_key(chunk_type, dataset, table, source)
        if not identity:
            continue
        item = grouped.setdefault(
            identity,
            {
                "id": dataset_id or identity,
                "label": dataset or dataset_id or table or source,
                "tables": [],
                "score": 0.0,
            },
        )
        item["score"] = max(float(item["score"]), score)
        if table and table not in item["tables"]:
            item["tables"].append(table)

    candidates = list(grouped.values())
    candidates.sort(key=lambda item: float(item["score"]), reverse=True)
    if not candidates or float(candidates[0]["score"]) < 0.75:
        return []
    top_score = float(candidates[0]["score"])
    close = [
        item
        for item in candidates
        if float(item["score"]) >= 0.75 and top_score - float(item["score"]) <= 0.08
    ]
    if len(close) < 2:
        return []
    for item in close:
        tables = item["tables"]
        table_text = f"相关表：{', '.join(tables[:4])}" if tables else ""
        score_text = f"最高相关度：{float(item['score']):.2f}"
        item["description"] = "；".join(part for part in (table_text, score_text) if part)
    return close


def extract_schema_ambiguity_candidates(tool_output: Any) -> List[Dict[str, str]]:
    """提取可用于 ask_user_question 单选卡的数据集候选。"""
    text = str(tool_output or "").strip()
    if not text:
        return []
    return [
        {
            "id": str(item["id"]),
            "label": str(item["label"]),
            "description": str(item.get("description") or ""),
        }
        for item in _new_schema_dataset_candidates(text)
    ]


def detect_schema_ambiguity(tool_output: Any) -> Tuple[bool, str]:
    """
    检测多个高置信度 Schema 候选是否分数接近且指向不同对象。
    兼容新旧分隔头格式。
    """
    text = str(tool_output or "").strip()
    if not text:
        return False, ""

    candidates: List[Tuple[float, str]] = [
        (float(item["score"]), str(item["id"]))
        for item in _new_schema_dataset_candidates(text)
    ]

    for score_text, source in _LEGACY_BLOCK_RE.findall(text):
        try:
            score = float(score_text)
        except ValueError:
            continue
        source_norm = source.strip().lower()
        if source_norm:
            candidates.append((score, source_norm))

    if len(candidates) < 2:
        return False, ""

    candidates.sort(reverse=True)
    top_score, top_key = candidates[0]
    close_candidates = [
        (score, key)
        for score, key in candidates[1:]
        if score >= 0.75 and top_score - score <= 0.08 and key != top_key
    ]
    if top_score >= 0.75 and close_candidates:
        display = ", ".join([top_key, *[key for _, key in close_candidates[:2]]])
        return True, f"多个高置信度 Schema 候选分数接近：{display}"
    return False, ""
