"""工具 JSON Schema 预检与语义等价净化。

背景：平台把 MCP 远端 ``inputSchema`` 原样透传给模型（`mcp_factory.py` 有意保留远端
原始 Schema，``args_schema`` 只管本地校验），而上游一旦启用 constrained decoding
（vLLM / xgrammar / outlines 一类）就会对 tools schema 做严格编译，遇到非法约束即整单
400——43 个工具里 1 个坏 schema 会废掉整轮请求，且上游返回的路径自带遮蔽
（``***.***.***.parameters/required``），点不出工具名。

因此这里做两件**不改变工具可用性**的事：

1. **预检点名**：把问题定位到具体工具与 schema 路径后写日志（同一问题每进程只报一次，
   避免每轮对话刷屏）；
2. **语义等价净化**：只修两类确定会被后端拒绝的约束——
   - 根层 ``parameters`` 缺 ``required`` 关键字时补 ``[]``：该关键字必须存在，空数组是
     后端接受的写法，缺失则整个请求 400（``***.***.***.parameters/required``）；
   - ``required`` 引用了 ``properties`` 中不存在的字段名（受限解码后端无法据此构造语法）。

**不做**的事：不摘工具、不改 ``properties``、不动其它关键字；``required: []`` 本身就是
合法写法（等价于无必填项），必须保留——删掉等于让根层丢掉这个关键字，反而触发 400；
``required`` 与 ``anyOf`` / ``oneOf`` / ``allOf`` / ``$ref`` 并列时无法静态确定合法字段集，
一律不碰；自引用（递归定义）的 schema 也不修改，但会点名报出——它同样是受限解码后端的
常见难点。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


#: 同一进程内已报告过的问题签名，避免每轮模型调用重复刷屏。
_REPORTED_ISSUES: set[str] = set()

#: 预检自身出错是否已报告（进程内只报一次，避免每轮刷屏）。
_PREFLIGHT_FAILURE_REPORTED = False

#: 与 ``required`` 并列时会让「合法字段集」无法静态确定的组合关键字。
_AMBIGUOUS_KEYS = ("anyOf", "oneOf", "allOf", "$ref", "enum", "const")

#: 值为「子 schema 映射」的关键字。
_MAP_KEYS = ("properties", "patternProperties", "$defs", "definitions")

#: 值为「子 schema 列表」的关键字。
_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")

#: 值为「单个子 schema」的关键字。
_SINGLE_KEYS = (
    "items",
    "additionalProperties",
    "not",
    "contains",
    "if",
    "then",
    "else",
    "propertyNames",
)

#: 递归深度上限：schema 本身不深，超过即视为异常输入，停止下钻。
_MAX_DEPTH = 60

#: 根层 schema 的路径标记；只有根层需要保证 ``required`` 关键字存在。
_ROOT_PATH = "$"

#: 本次调用中「根层缺 required 被补」的工具名（签名去重，按序遍历顺序）；在
#: ``sanitize_tool_schemas`` 返回前汇总成一条日志——缺 required 是常态（多数
#: MCP 工具都不写这个关键字），逐个工具点名会让首轮刷出几十条。
_PENDING_MISSING_REQUIRED: list[str] = []


def sanitize_tool_schemas(tools: Any) -> Any:
    """预检并净化 OpenAI 形态的 tools schema 列表。

    传入 ``[{"type": "function", "function": {"name", "parameters"}}]``，返回修复后的
    列表；**没有任何改动时原样返回入参**（保持对象身份，不做无谓拷贝）。

    **预检绝不会反过来打断模型调用**：畸形输入按原样放行，预检自身出现未预料的缺陷时
    也整体兜住并退回未净化的入参（只留一条日志），因为它的异常会从 ``_format_tools``
    冒出、让整轮请求直接失败——那比原本的 400 更糟。
    """
    if not isinstance(tools, list):
        return tools
    try:
        result = _sanitize_tool_list(tools)
    except Exception:
        _report_preflight_failure()
        result = tools
    finally:
        # 汇总日志必须发出去：即便上面走了兜底，已补过的工具也要留下痕迹。
        _flush_missing_required()
    return result


def _sanitize_tool_list(tools: list[Any]) -> Any:
    repaired: list[Any] | None = None
    for index, tool in enumerate(tools):
        new_tool = _sanitize_tool(tool)
        if new_tool is tool:
            if repaired is not None:
                repaired.append(tool)
            continue
        if repaired is None:
            repaired = list(tools[:index])
        repaired.append(new_tool)

    return tools if repaired is None else repaired


def _flush_missing_required() -> None:
    """把「根层补了 required」的工具汇总成一条日志。

    缺 ``required`` 是常态，逐个工具点名会让首轮刷出几十条；汇总成一条既保留
    「补了哪些工具」的可观测性，又不吵。
    """
    if not _PENDING_MISSING_REQUIRED:
        return
    names = list(_PENDING_MISSING_REQUIRED)
    _PENDING_MISSING_REQUIRED.clear()
    logger.warning(
        "[AgentScope] Tool schema issue: kind=missing_required path=$.required "
        "action=repaired count=%d tools=%s (upstream requires the keyword to be present)",
        len(names),
        ", ".join(names),
    )


def _report_preflight_failure() -> None:
    """预检自身出错：进程内只报一次（含堆栈），避免每轮对话刷屏。"""
    global _PREFLIGHT_FAILURE_REPORTED
    if _PREFLIGHT_FAILURE_REPORTED:
        return
    _PREFLIGHT_FAILURE_REPORTED = True
    logger.warning(
        "[AgentScope] Tool schema preflight failed; sending tool schemas as-is",
        exc_info=True,
    )


def _sanitize_tool(tool: Any) -> Any:
    if not isinstance(tool, dict):
        return tool
    func = tool.get("function")
    if not isinstance(func, dict):
        return tool
    parameters = func.get("parameters")
    if not isinstance(parameters, dict):
        return tool

    name = func.get("name")
    tool_name = str(name) if name else "unknown"
    new_parameters = _sanitize_schema(
        parameters,
        tool_name=tool_name,
        path="$",
        depth=0,
        seen=frozenset(),
    )
    if new_parameters is parameters:
        return tool
    return {**tool, "function": {**func, "parameters": new_parameters}}


def _sanitize_schema(
    node: Any,
    *,
    tool_name: str,
    path: str,
    depth: int,
    seen: frozenset[int],
) -> Any:
    if not isinstance(node, dict) or depth > _MAX_DEPTH:
        return node
    if id(node) in seen:
        # 自引用（树形参数的递归定义）：停止下钻、原样保留该节点，并把这一事实点名
        # 报出来——递归 schema 是受限解码后端的常见难点，也可能是上游拒绝的原因。
        _report(
            tool_name,
            kind="cyclic_schema",
            path=path,
            action="kept",
            detail="self-referencing schema detected; stopped descending",
        )
        return node
    seen = seen | {id(node)}

    fixed_required = _repair_required(node, tool_name=tool_name, path=path)
    if fixed_required is not None:
        node = {**node, "required": fixed_required}

    updates: dict[str, Any] = {}
    for key, value in node.items():
        if key in _MAP_KEYS and isinstance(value, dict):
            new_value = _sanitize_schema_map(
                value,
                tool_name=tool_name,
                path=f"{path}.{key}",
                depth=depth + 1,
                seen=seen,
            )
        elif key in _LIST_KEYS and isinstance(value, list):
            new_value = _sanitize_schema_list(
                value,
                tool_name=tool_name,
                path=f"{path}.{key}",
                depth=depth + 1,
                seen=seen,
            )
        elif key in _SINGLE_KEYS and isinstance(value, dict):
            new_value = _sanitize_schema(
                value,
                tool_name=tool_name,
                path=f"{path}.{key}",
                depth=depth + 1,
                seen=seen,
            )
        else:
            continue
        if new_value is not value:
            updates[key] = new_value

    if updates:
        return {**node, **updates}
    return node


def _sanitize_schema_map(
    mapping: dict[str, Any],
    *,
    tool_name: str,
    path: str,
    depth: int,
    seen: frozenset[int],
) -> Any:
    updates: dict[str, Any] = {}
    for key, value in mapping.items():
        if not isinstance(value, dict):
            continue
        new_value = _sanitize_schema(
            value,
            tool_name=tool_name,
            path=f"{path}.{key}",
            depth=depth,
            seen=seen,
        )
        if new_value is not value:
            updates[key] = new_value
    if not updates:
        return mapping
    return {**mapping, **updates}


def _sanitize_schema_list(
    items: list[Any],
    *,
    tool_name: str,
    path: str,
    depth: int,
    seen: frozenset[int],
) -> Any:
    updates: dict[int, Any] = {}
    for index, value in enumerate(items):
        if not isinstance(value, dict):
            continue
        new_value = _sanitize_schema(
            value,
            tool_name=tool_name,
            path=f"{path}[{index}]",
            depth=depth,
            seen=seen,
        )
        if new_value is not value:
            updates[index] = new_value
    if not updates:
        return items
    result = list(items)
    for index, value in updates.items():
        result[index] = value
    return result


def _repair_required(
    node: dict[str, Any],
    *,
    tool_name: str,
    path: str,
) -> list[Any] | None:
    """返回本层 ``required`` 修复后的取值，或 ``None`` 表示不动。"""
    if "required" not in node:
        # 只有根层补：上游要求 function.parameters 带 required 这个关键字，缺了整个
        # 请求 400。嵌套属性本来就不写 required，补了只是噪音。
        if path != _ROOT_PATH:
            return None
        _report(
            tool_name,
            kind="missing_required",
            path=f"{path}.required",
            detail="added empty 'required' (upstream requires the keyword to be present)",
        )
        return []
    required = node.get("required")
    if not isinstance(required, list):
        # 形态异常（如 required: "bad"）：不是我们能安全推断的输入，不动。
        return None

    required_path = f"{path}.required"
    if not required:
        # `required: []` 是合法写法（等价于无必填项），上游也接受，必须保留。
        return None

    properties = node.get("properties")
    if not isinstance(properties, dict):
        # 自由字典（无 properties）无法判断字段名是否有定义，宁可不改。
        return None
    if any(key in node for key in _AMBIGUOUS_KEYS):
        # required 与 anyOf / $ref 等并列：合法字段集可能定义在分支里，静态不可判定。
        return None

    undefined = [
        item
        for item in required
        if isinstance(item, str) and item not in properties
    ]
    if not undefined:
        return None

    kept = [
        item
        for item in required
        if not (isinstance(item, str) and item not in properties)
    ]
    _report(
        tool_name,
        kind="required_undefined_property",
        path=required_path,
        detail=(
            "dropped required name(s) absent from properties: "
            + ", ".join(undefined)
        ),
    )
    return kept


def _report(
    tool_name: str,
    *,
    kind: str,
    path: str,
    detail: str,
    action: str = "repaired",
) -> None:
    signature = f"{tool_name}|{kind}|{path}|{detail}"
    if signature in _REPORTED_ISSUES:
        return
    _REPORTED_ISSUES.add(signature)
    if kind == "missing_required":
        # 「补 required」单独汇总（见 _flush_missing_required），不逐条打印。
        _PENDING_MISSING_REQUIRED.append(tool_name)
        return
    logger.warning(
        "[AgentScope] Tool schema issue: tool=%s kind=%s path=%s "
        "action=%s detail=%s",
        tool_name,
        kind,
        path,
        action,
        detail,
    )
