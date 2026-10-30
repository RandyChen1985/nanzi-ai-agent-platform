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
    messages: Any = None,
) -> Optional[list[str]]:
    """本轮范围优先，否则会话挂载；皆空返回 None（不注入白名单）。"""
    request = merge_request_metadata_dataset_ids(request_ids=request_ids, messages=messages)
    if request:
        return request
    session = _normalize_id_list(session_ids)
    return session or None


def merge_request_metadata_dataset_ids(
    request_ids: Any = None,
    messages: Any = None,
) -> Optional[list[str]]:
    """顶层本轮 IDs 优先；缺失时仅从最后一条用户消息附件中提取范围。"""
    ids = _normalize_id_list(request_ids)
    if ids:
        return ids
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        for f in msg.get("files") or []:
            if not isinstance(f, dict) or f.get("type") != "metadata_dataset":
                continue
            for item in str(f.get("url") or "").split(","):
                value = item.strip()
                if value and value not in ids:
                    ids.append(value)
        break
    return ids or None

def normalize_bound_dataset_ids(raw: Any) -> list[str]:
    """规整 ``get_dataset_schema.metadata_dataset_ids`` 绑定值，只保留有效数据集 ID。

    这是**唯一实现**：``get_dataset_schema`` 入口用它过滤后再去查权限，列表接口用它
    统计「已绑定 N 个数据集」。两边口径必须一致，否则会出现「横幅说绑了 3 个、实际
    只有 1 个生效」——绑定值写成数据集名称时，运行时会被静默丢弃，所以这里也不计入。

    返回纯数字 ID（去重、保序）；全部无效时返回空列表（等价于未绑定）。
    """
    return [value for value in _normalize_id_list(raw) if value.isdigit()]
