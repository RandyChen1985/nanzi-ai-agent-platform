"""定时任务执行选项：从 task.config 解析模型、批准方式与资源范围。"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

APPROVAL_MODE_KEY = "approval_mode"
MODEL_KEY = "model"
RESOURCE_SCOPE_KEY = "resource_scope"
THINKING_ENABLE_KEY = "thinking_enable"
REASONING_EFFORT_KEY = "reasoning_effort"
TEMPERATURE_KEY = "temperature"
VALID_APPROVAL_MODES = frozenset({"ask", "allow", "deny"})
VALID_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
DEFAULT_APPROVAL_MODE = "allow"


def normalize_approval_mode(raw: Any, *, default: str = DEFAULT_APPROVAL_MODE) -> str:
    value = str(raw or "").strip().lower()
    if value in VALID_APPROVAL_MODES:
        return value
    return default if default in VALID_APPROVAL_MODES else DEFAULT_APPROVAL_MODE


def normalize_model_id(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def normalize_thinking_enable(raw: Any) -> Optional[bool]:
    """只接受任务 UI 写入的布尔覆盖值，不把字符串隐式转换成布尔值。"""
    return raw if isinstance(raw, bool) else None


def normalize_reasoning_effort(raw: Any) -> Optional[str]:
    value = str(raw or "").strip().lower()
    return value if value in VALID_REASONING_EFFORTS else None


def normalize_temperature(raw: Any) -> Optional[float]:
    """规范化任务配置中的采样温度，有效范围 0.0 ~ 2.0，四舍五入保留两位小数。"""
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
        if math.isnan(val) or math.isinf(val):
            return None
        return round(max(0.0, min(val, 2.0)), 2)
    except (TypeError, ValueError):
        return None


def _normalize_scope_items(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    items: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or entry.get("name") or "").strip()
        name = str(entry.get("name") or entry.get("display_name") or item_id).strip()
        if not item_id and not name:
            continue
        normalized = dict(entry)
        normalized["id"] = item_id or name
        normalized["name"] = name or item_id
        items.append(normalized)
    return items


def normalize_resource_scope(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, Mapping) else {}
    return {
        "project_name": str(source.get("project_name") or "").strip(),
        "datasets": _normalize_scope_items(source.get("datasets")),
        "knowledge_bases": _normalize_scope_items(source.get("knowledge_bases")),
        "skills": _normalize_scope_items(source.get("skills")),
        "mcp_tools": _normalize_scope_items(source.get("mcp_tools")),
    }


def resource_scope_from_task_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    cfg = config if isinstance(config, Mapping) else {}
    return normalize_resource_scope(cfg.get(RESOURCE_SCOPE_KEY))


def permission_options_from_task_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    cfg = config if isinstance(config, Mapping) else {}
    return {"approval_mode": normalize_approval_mode(cfg.get(APPROVAL_MODE_KEY))}


def model_from_task_config(config: Optional[Mapping[str, Any]]) -> Optional[str]:
    cfg = config if isinstance(config, Mapping) else {}
    return normalize_model_id(cfg.get(MODEL_KEY) or cfg.get("model_id"))


def knowledge_dataset_ids_from_scope(scope: Mapping[str, Any]) -> List[str]:
    ids: List[str] = []
    seen = set()
    for item in scope.get("knowledge_bases") or []:
        if not isinstance(item, Mapping):
            continue
        for key in ("id", "dataset_id", "ragflow_dataset_id", "dataset_name", "name"):
            value = str(item.get(key) or "").strip()
            if value and value not in seen:
                seen.add(value)
                ids.append(value)
                break
    return ids


def metadata_dataset_ids_from_scope(scope: Mapping[str, Any]) -> List[str]:
    ids: List[str] = []
    seen = set()
    for item in scope.get("datasets") or []:
        if not isinstance(item, Mapping):
            continue
        for key in ("id", "dataset_name", "name"):
            value = str(item.get(key) or "").strip()
            if value and value not in seen:
                seen.add(value)
                ids.append(value)
                break
    return ids


def debug_options_from_task_config(config: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    cfg = config if isinstance(config, Mapping) else {}
    scope = resource_scope_from_task_config(cfg)
    options: Dict[str, Any] = {"resource_scope": scope}
    model_id = model_from_task_config(cfg)
    if model_id:
        options["model"] = model_id
    thinking_enable = normalize_thinking_enable(cfg.get(THINKING_ENABLE_KEY))
    if THINKING_ENABLE_KEY in cfg and thinking_enable is not None:
        options[THINKING_ENABLE_KEY] = thinking_enable
    reasoning_effort = normalize_reasoning_effort(cfg.get(REASONING_EFFORT_KEY))
    if reasoning_effort and thinking_enable is not False:
        options[REASONING_EFFORT_KEY] = reasoning_effort
    temperature = normalize_temperature(cfg.get(TEMPERATURE_KEY))
    if TEMPERATURE_KEY in cfg and temperature is not None:
        options[TEMPERATURE_KEY] = temperature
    return options


def merge_execution_options_into_config(
    config: Optional[Mapping[str, Any]],
    *,
    approval_mode: Any = None,
    model: Any = None,
    resource_scope: Any = None,
    temperature: Any = None,
) -> Dict[str, Any]:
    merged = dict(config or {})
    if approval_mode is not None:
        merged[APPROVAL_MODE_KEY] = normalize_approval_mode(approval_mode)
    if model is not None:
        model_id = normalize_model_id(model)
        if model_id:
            merged[MODEL_KEY] = model_id
        else:
            merged.pop(MODEL_KEY, None)
            merged.pop("model_id", None)
    if temperature is not None:
        temp_val = normalize_temperature(temperature)
        if temp_val is not None:
            merged[TEMPERATURE_KEY] = temp_val
        else:
            merged.pop(TEMPERATURE_KEY, None)
    if resource_scope is not None:
        scope = normalize_resource_scope(resource_scope)
        has_any = bool(
            scope["project_name"]
            or scope["datasets"]
            or scope["knowledge_bases"]
            or scope["skills"]
            or scope["mcp_tools"]
        )
        if has_any:
            merged[RESOURCE_SCOPE_KEY] = scope
        else:
            merged.pop(RESOURCE_SCOPE_KEY, None)
    return merged
