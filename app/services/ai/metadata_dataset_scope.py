"""本轮 / 会话 MetaDataset 范围合并。"""

from __future__ import annotations

from typing import Any, Optional


def _normalize_id_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (str, int)):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def resolve_effective_metadata_dataset_ids(
    *,
    request_ids: Any = None,
    session_ids: Any = None,
) -> Optional[list[str]]:
    """本轮非空优先，否则会话挂载；皆空返回 None（不注入白名单）。"""
    request = _normalize_id_list(request_ids)
    if request:
        return request
    session = _normalize_id_list(session_ids)
    return session or None
