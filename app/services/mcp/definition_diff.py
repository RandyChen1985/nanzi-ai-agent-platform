"""MCP 工具定义指纹与差异计算（纯函数，无 IO、无 ORM）。

这一层刻意做成零依赖，因为有三处都要用它，而它们之间不能互相 import：

- ``mcp_client.sync_tools()`` —— 应用差异时写库，必须同时更新指纹，否则探测会立刻
  再次报「描述变更」；
- ``services/mcp/definition_probe.py`` —— 只读探测，算 diff 但不写业务字段；
- 接口层 —— 把落库的 before/after 片段渲染成一句中文摘要。

关于「为什么要指纹而不是比字段」：``sys_mcp_tool_cache.updated_at`` 是
``ON UPDATE CURRENT_TIMESTAMP``，只在平台主动写库时变；远端悄悄改了描述，它不会动。
所以必须存一份「已确认的远端定义」的指纹，探测时拿远端当前定义的指纹跟它比。

指纹口径：``description + canonical(schema) + canonical(annotations)`` 的 sha256。
规范化做三件事，缺一个就会天天报假变更：

1. 递归剥掉平台私有扩展 ``x-nanzi-*``（那是平台自己写的调用门禁，远端不该参与比对）；
2. ``json.dumps(sort_keys=True)``，消除 dict 键顺序差异；
3. 固定分隔符与 ``ensure_ascii=False``，消除空白与转义差异。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

#: 平台私有 JSON-Schema 扩展前缀。这些字段决定「证据类型 / 证据策略」等调用门禁，
#: 只能由平台自己写；远端服务在 inputSchema 里自带同名键时必须剥掉，否则等于让远端
#: 改写平台的调用策略。
PLATFORM_SCHEMA_EXTENSION_PREFIX = "x-nanzi-"

#: 变更类型 → 严重度。``param_required`` / ``param_enum`` 会按变化方向动态覆盖
#: （必填→可选是放宽，枚举新增值一般安全；反向才是破坏性）。
SEVERITY_BY_KIND: Dict[str, str] = {
    "description_changed": "safe",
    "param_description": "safe",
    "param_added_optional": "safe",
    "param_enum": "notice",
    "param_required": "notice",
    "param_removed": "breaking",
    "param_type": "breaking",
    "tool_added": "notice",
    "tool_removed": "breaking",
    "annotations_changed": "notice",
}

#: 会阻塞「无人值守自动应用」的类型；也是前端点红标的依据。
BREAKING_KINDS = frozenset({"param_removed", "param_type", "tool_removed"})


def strip_platform_schema_extensions(value: Any) -> Any:
    """递归移除 schema 里的平台私有扩展键（``x-nanzi-*``）。"""
    if isinstance(value, dict):
        return {
            key: strip_platform_schema_extensions(item)
            for key, item in value.items()
            if not (
                isinstance(key, str)
                and key.strip().casefold().startswith(PLATFORM_SCHEMA_EXTENSION_PREFIX)
            )
        }
    if isinstance(value, list):
        return [strip_platform_schema_extensions(item) for item in value]
    return value


def tool_input_schema(tool: Any) -> Any:
    """取 MCP 工具的入参 schema。

    mcp 2.x 把 JSON-RPC 的 ``inputSchema`` 字段改名为 ``input_schema``（与
    ``read_only_hint`` 等注解一起）；但外部服务返回的原始 dict、以及历史缓存里
    仍可能是旧名。这里按「新名 → 旧名 → 平台缓存字段」依次回退，两个大版本都能取到。
    """
    for attr in ("input_schema", "inputSchema"):
        value = getattr(tool, attr, None)
        if value is not None:
            return value
    if isinstance(tool, dict):
        for key in ("input_schema", "inputSchema", "parameter_schema"):
            value = tool.get(key)
            if value is not None:
                return value
    return None


def tool_annotations(tool: Any) -> Optional[Dict[str, Any]]:
    """取 MCP 工具的 annotations（可能为 None）。"""
    value = getattr(tool, "annotations", None)
    if value is None and isinstance(tool, dict):
        value = tool.get("annotations")
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return value if isinstance(value, dict) and value else None


def canonical_schema(schema: Any) -> Dict[str, Any]:
    """把任意 schema 归一成可稳定比较的 dict。"""
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump(mode="json")
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except (TypeError, ValueError):
            return {}
    if not isinstance(schema, dict):
        return {}
    cleaned = strip_platform_schema_extensions(schema)
    return cleaned if isinstance(cleaned, dict) else {}


def canonical_annotations(annotations: Any) -> Dict[str, Any]:
    """把 annotations 归一成可稳定比较的 dict（空视为「没有」，避免 None / {} 互报差异）。"""
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(mode="json")
    if isinstance(annotations, str):
        try:
            annotations = json.loads(annotations)
        except (TypeError, ValueError):
            return {}
    if not isinstance(annotations, dict):
        return {}
    return strip_platform_schema_extensions(annotations)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def definition_fingerprint(
    description: Optional[str],
    schema: Any,
    annotations: Any = None,
) -> str:
    """计算工具定义的指纹（sha256 hex）。

    传进来的 ``schema`` 既可以是远端 ``input_schema`` 对象，也可以是库里存的
    ``parameter_schema`` 文本——两者都会先归一，所以「远端算一次、库里算一次」得到
    同一个值，这是判断「变了没」的前提。
    """
    payload = {
        "description": (description or "").strip(),
        "schema": canonical_schema(schema),
        "annotations": canonical_annotations(annotations),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _schema_properties(schema: Dict[str, Any]) -> Dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _required_names(schema: Dict[str, Any]) -> set:
    required = schema.get("required")
    if not isinstance(required, list):
        return set()
    return {str(item) for item in required if isinstance(item, str)}


def _schema_defs(schema: Dict[str, Any]) -> Dict[str, Any]:
    defs: Dict[str, Any] = {}
    for key in ("definitions", "$defs"):
        block = schema.get(key)
        if isinstance(block, dict):
            defs.update(block)
    return defs


def _resolve_ref_node(
    node: Any,
    defs: Dict[str, Any],
    *,
    seen: Optional[set] = None,
    depth: int = 0,
) -> Any:
    """展开 JSON Schema 的本地 ``$ref``（如 ``#/$defs/MyModel``），供参数差异比对使用。

    加环检测与深度上限，避免自引用树形模型导致无限递归。
    """
    if depth > 12:
        # 上限存在的意义只是防环自爆；定成 6 会让「$ref 落在第 7 层」这类正常模型
        # 停在 {"$ref": ...} 字面量上——指纹变了却报不出任何差异，变更永远不进待办。
        return node
    if isinstance(node, list):
        return [_resolve_ref_node(item, defs, seen=seen, depth=depth + 1) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        visited = set(seen or ())
        if ref not in visited:
            parts = ref[2:].split("/")
            target: Any = None
            if len(parts) == 2 and parts[0] in ("$defs", "definitions"):
                target = defs.get(parts[1])
            if isinstance(target, dict):
                visited.add(ref)
                merged = dict(target)
                for k, v in node.items():
                    if k != "$ref":
                        merged[k] = v
                return _resolve_ref_node(merged, defs, seen=visited, depth=depth + 1)

    resolved: Dict[str, Any] = {}
    for k, v in node.items():
        if k in ("properties", "items", "anyOf", "oneOf", "allOf", "additionalProperties"):
            if k == "properties" and isinstance(v, dict):
                resolved[k] = {
                    pk: _resolve_ref_node(pv, defs, seen=seen, depth=depth + 1)
                    for pk, pv in v.items()
                }
            else:
                resolved[k] = _resolve_ref_node(v, defs, seen=seen, depth=depth + 1)
        else:
            resolved[k] = v
    return resolved


def _param_snapshot(
    prop: Any,
    required: bool,
    *,
    defs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """参数快照：保留所有影响调用契约的字段（含嵌套结构与 ``$ref`` 展开结果）。"""
    if not isinstance(prop, dict):
        return {"required": required}
    effective = _resolve_ref_node(prop, defs or {}) if defs else prop
    if not isinstance(effective, dict):
        effective = prop
    snapshot: Dict[str, Any] = {
        "type": effective.get("type"),
        "required": required,
    }
    # anyOf / oneOf / allOf / $ref / properties / required / additionalProperties 必须一起采：
    # FastMCP / Pydantic v2 的 Optional、Enum、嵌套对象参数依赖这些字段表达约束，
    # 漏采会让破坏性嵌套结构变更被指纹感知却产出空 diff。
    for key in (
        "enum", "items", "default", "format", "minimum", "maximum",
        "anyOf", "oneOf", "allOf", "const",
        "$ref", "properties", "additionalProperties",
        "pattern", "minLength", "maxLength",
    ):
        if key in effective:
            snapshot[key] = effective.get(key)
    if "required" in effective and isinstance(effective.get("required"), list):
        snapshot["nested_required"] = sorted(
            str(item) for item in effective["required"] if isinstance(item, str)
        )
    description = effective.get("description")
    if isinstance(description, str) and description.strip():
        snapshot["description"] = description.strip()
    return snapshot


def _strip_descriptions(value: Any) -> Any:
    """递归剥掉 ``description``，用于区分「嵌套字段只是改了说明」与「结构真变了」。

    快照里现在带着整棵 ``properties``（含每个子字段的 description）。不剥的话，
    远端给某个子字段补一句说明就会被判成 ``param_type`` / ``breaking``——纯文档
    改动进「破坏性变更」统计，人工确认队列会被淹没，红标也失去意义。
    """
    if isinstance(value, dict):
        return {
            key: _strip_descriptions(item)
            for key, item in value.items()
            if key != "description"
        }
    if isinstance(value, list):
        return [_strip_descriptions(item) for item in value]
    return value


def _param_summary(kind: str, name: str, before: Optional[dict], after: Optional[dict]) -> str:
    if kind == "param_removed":
        return f"删除参数 {name}"
    if kind == "param_added_optional":
        return f"新增可选参数 {name}"
    if kind == "param_required":
        if before is None:
            # 列表页用 summarize_change 从落库片段重算摘要，此刻 before 是 None；
            # 不区分就会把「新增必填参数」说成「从可选变为必填」。
            return f"新增必填参数 {name}"
        became_required = bool((after or {}).get("required"))
        return (
            f"参数 {name} 从可选变为必填" if became_required else f"参数 {name} 从必填变为可选"
        )
    if kind == "param_type":
        before_type = (before or {}).get("type")
        after_type = (after or {}).get("type")
        if before_type != after_type:
            return f"参数 {name} 类型 {before_type} → {after_type}"
        labels = {
            "items": "子结构",
            "properties": "嵌套字段",
            "nested_required": "嵌套必填项",
            "additionalProperties": "扩展字段策略",
            "$ref": "引用类型",
            "allOf": "组合类型",
            "format": "格式",
            "minimum": "最小值",
            "maximum": "最大值",
            "minLength": "最小长度",
            "maxLength": "最大长度",
            "pattern": "正则约束",
            "default": "默认值",
            "anyOf": "类型",
            "oneOf": "类型",
            "const": "固定值",
        }
        changed = [
            labels.get(key, key)
            for key in (
                "items", "properties", "nested_required", "additionalProperties",
                "$ref", "allOf", "anyOf", "oneOf", "const",
                "format", "minimum", "maximum", "minLength", "maxLength", "pattern", "default",
            )
            if (before or {}).get(key) != (after or {}).get(key)
        ]
        # type 相同却走到这里，说明是约束变了；再说「类型 integer → integer」纯属噪音
        return f"参数 {name} " + "、".join(dict.fromkeys(changed) or ["约束"]) + "变更"
    if kind == "param_description":
        # 顶层 description 没变、变的是嵌套字段的说明时，说成「描述变更」会让人
        # 以为是这个参数自己的描述被改了，找不到改在哪一层。
        if (before or {}).get("description") == (after or {}).get("description"):
            return f"参数 {name} 的嵌套字段说明变更"
        return f"参数 {name} 描述变更"
    if kind == "param_enum":
        raw_before = (before or {}).get("enum")
        raw_after = (after or {}).get("enum")
        before_enum = raw_before if isinstance(raw_before, list) else []
        after_enum = raw_after if isinstance(raw_after, list) else []
        added = [v for v in after_enum if v not in before_enum]
        removed = [v for v in before_enum if v not in after_enum]
        parts = []
        if added:
            parts.append("新增 " + "、".join(str(v) for v in added))
        if removed:
            parts.append("移除 " + "、".join(str(v) for v in removed))
        return f"参数 {name} 枚举值" + ("，".join(parts) if parts else "变更")
    return f"参数 {name} 变更"


def _enum_change_severity(before: Optional[dict], after: Optional[dict]) -> str:
    """枚举：只新增取值是放宽（safe）；删掉取值会让老调用失效（breaking）。

    「原来不限取值、现在给出白名单」同样是收紧——老调用传任何别的值都会被拒——
    按「removed 为空 = safe」判会把它误标成安全。
    """
    raw_before = (before or {}).get("enum")
    raw_after = (after or {}).get("enum")
    before_enum = raw_before if isinstance(raw_before, list) else []
    after_enum = raw_after if isinstance(raw_after, list) else []
    # 两个方向都要单独判：只看「有没有移除取值」时，
    #   无枚举 → 有枚举（收紧）与 有枚举 → 无枚举（放宽）都是 removed 为空/非空的反面，
    # 漏掉任何一边都会把兼容性变化标反。
    if not before_enum and after_enum:
        return "breaking"
    if before_enum and not after_enum:
        return "safe"
    removed = [v for v in before_enum if v not in after_enum]
    return "breaking" if removed else "safe"


def diff_parameter_schemas(
    before_schema: Any,
    after_schema: Any,
) -> List[Dict[str, Any]]:
    """按参数粒度比较两份 schema，返回变更列表。

    粒度刻意做到参数级而不是「整块 schema 变了」：整块报差异时，人只看到
    「schema 变了」，无法判断是加了个可选参数（无感）还是把可选改成必填
    （已绑定的智能体下次就调不通）。这个区别决定了要不要立刻处理。
    """
    before = canonical_schema(before_schema)
    after = canonical_schema(after_schema)
    before_defs = _schema_defs(before)
    after_defs = _schema_defs(after)
    before_props = _schema_properties(before)
    after_props = _schema_properties(after)
    before_required = _required_names(before)
    after_required = _required_names(after)

    changes: List[Dict[str, Any]] = []

    for name in sorted(set(after_props) - set(before_props)):
        snapshot = _param_snapshot(after_props[name], name in after_required, defs=after_defs)
        kind = "param_required" if name in after_required else "param_added_optional"
        severity = "breaking" if name in after_required else "safe"
        changes.append(
            {
                "kind": kind,
                "severity": severity,
                "param": name,
                "before": None,
                "after": snapshot,
                "summary": (
                    f"新增必填参数 {name}" if name in after_required else _param_summary(kind, name, None, snapshot)
                ),
            }
        )

    for name in sorted(set(before_props) - set(after_props)):
        snapshot = _param_snapshot(before_props[name], name in before_required, defs=before_defs)
        changes.append(
            {
                "kind": "param_removed",
                "severity": "breaking",
                "param": name,
                "before": snapshot,
                "after": None,
                "summary": _param_summary("param_removed", name, snapshot, None),
            }
        )

    for name in sorted(set(before_props) & set(after_props)):
        before_snapshot = _param_snapshot(before_props[name], name in before_required, defs=before_defs)
        after_snapshot = _param_snapshot(after_props[name], name in after_required, defs=after_defs)
        if before_snapshot == after_snapshot:
            continue

        # 一个参数可能同时改多处（类型 + 必填），按「最严重」的报一条，避免同一参数
        # 在列表里出现三次、让人以为有三个问题。
        detected: List[tuple] = []
        if before_snapshot.get("type") != after_snapshot.get("type"):
            detected.append(("param_type", "breaking"))
        if before_snapshot.get("required") != after_snapshot.get("required"):
            became_required = bool(after_snapshot.get("required"))
            detected.append(("param_required", "breaking" if became_required else "notice"))
        if before_snapshot.get("enum") != after_snapshot.get("enum"):
            detected.append(("param_enum", _enum_change_severity(before_snapshot, after_snapshot)))
        for key in (
            "items", "anyOf", "oneOf", "allOf", "const",
            "$ref", "nested_required", "additionalProperties",
        ):
            if before_snapshot.get(key) != after_snapshot.get(key):
                # 子结构 / 联合类型变了：调用方按旧类型传值会失败
                detected.append(("param_type", "breaking"))
                break
        if before_snapshot.get("properties") != after_snapshot.get("properties"):
            # 嵌套字段：结构变了才算破坏性，只剩 description 变了按「文档改动」处理。
            # 不区分的话，远端给某个子字段补一句说明就会被标成 breaking，
            # 「破坏性变更」统计与人工确认队列都会被纯文档改动刷屏。
            if _strip_descriptions(before_snapshot.get("properties")) != _strip_descriptions(
                after_snapshot.get("properties")
            ):
                detected.append(("param_type", "breaking"))
            else:
                detected.append(("param_description", "safe"))
        for key in ("format", "minimum", "maximum", "minLength", "maxLength", "pattern", "default"):
            if before_snapshot.get(key) != after_snapshot.get(key):
                # 约束/默认值变化不会让签名对不上，但会影响调用结果——标 notice 而不是
                # 一律 breaking，否则「默认值改了一下」也会进「破坏性」统计。
                detected.append(("param_type", "notice"))
                break
        if before_snapshot.get("description") != after_snapshot.get("description"):
            detected.append(("param_description", "safe"))

        if not detected:
            continue
        rank = {"breaking": 0, "notice": 1, "safe": 2}
        kind, severity = sorted(detected, key=lambda item: rank.get(item[1], 9))[0]
        changes.append(
            {
                "kind": kind,
                "severity": severity,
                "param": name,
                "before": before_snapshot,
                "after": after_snapshot,
                "summary": _param_summary(kind, name, before_snapshot, after_snapshot),
            }
        )

    return changes


def diff_tool_definition(
    *,
    tool_name: str,
    before_description: Optional[str],
    before_schema: Any,
    before_annotations: Any = None,
    after_description: Optional[str],
    after_schema: Any,
    after_annotations: Any = None,
) -> List[Dict[str, Any]]:
    """比较单个工具的完整定义，返回变更列表（可能为空）。

    返回项统一形如::

        {
          "kind": "param_required",
          "severity": "breaking",
          "param": "contract_no",
          "before": {...}, "after": {...},
          "summary": "参数 contract_no 从可选变为必填",
          "tool_name": "server:tool",
          "definition_hash": "<远端当前定义的指纹>",
        }

    ``definition_hash`` 跟着每条变更走，是为了让「忽略」这条变更时能把「已确认的远端
    版本」推进到该指纹——否则远端不动、人已确认，下次巡检还会报同一件事。
    """
    remote_hash = definition_fingerprint(
        after_description, after_schema, after_annotations
    )
    changes: List[Dict[str, Any]] = []

    before_desc = (before_description or "").strip()
    after_desc = (after_description or "").strip()
    if before_desc != after_desc:
        changes.append(
            {
                "kind": "description_changed",
                "severity": "safe",
                "param": None,
                "before": {"description": before_description},
                "after": {"description": after_description},
                "summary": "工具描述变更",
            }
        )

    changes.extend(diff_parameter_schemas(before_schema, after_schema))

    if canonical_annotations(before_annotations) != canonical_annotations(after_annotations):
        changes.append(
            {
                "kind": "annotations_changed",
                "severity": "notice",
                "param": None,
                "before": canonical_annotations(before_annotations) or None,
                "after": canonical_annotations(after_annotations) or None,
                "summary": "工具标注（annotations）变更",
            }
        )

    for change in changes:
        change["tool_name"] = tool_name
        change["definition_hash"] = remote_hash
    return changes


def summarize_change(row_kind: str, before_value: Any, after_value: Any) -> str:
    """把落库的 before/after 片段还原成一句中文摘要（列表与通知复用）。

    摘要不落库：文案会随产品口径调整，存进表里就成了需要迁移才能改的历史包袱。
    """
    before = _coerce_json(before_value)
    after = _coerce_json(after_value)
    param = None
    if isinstance(after, dict):
        param = after.get("param")
    if not param and isinstance(before, dict):
        param = before.get("param")

    if row_kind == "description_changed":
        return "工具描述变更"
    if row_kind == "annotations_changed":
        return "工具标注（annotations）变更"
    if row_kind == "tool_added":
        return "远端新增工具"
    if row_kind == "tool_removed":
        return "远端已删除该工具"
    if param:
        return _param_summary(row_kind, str(param), before, after)
    return "工具定义变更"


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value
