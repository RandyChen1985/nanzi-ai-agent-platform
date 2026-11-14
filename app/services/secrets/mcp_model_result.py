"""按配置的 JSONPath 原样投影 MCP 业务结果。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from jsonpath_rfc9535 import JSONPathError, JSONPathQuery, JSONPathRecursionError
from jsonpath_rfc9535.segments import JSONPathChildSegment
from jsonpath_rfc9535.selectors import IndexSelector, NameSelector, WildcardSelector

from app.services.secrets.mcp_jsonpath import (
    QueryArray,
    QueryBudget,
    QueryLimitExceeded,
    QueryObject,
    ResultQueryEnvironment,
)

MAX_RESULT_FIELDS = 20
MAX_FIELD_PATH = 256
_MAX_DEPTH = 32
_MAX_RESPONSE_BYTES = 512 * 1024
_MAX_RESULT_BYTES = 48 * 1024
_FIELD_PART = re.compile(r"([A-Za-z_][A-Za-z0-9_-]{0,63})(\[\])?")
_SELECTOR_ERROR = "请填写有效的 JSONPath，例如 $、$.messages[*] 或 $.messages[*][1]"
_SELECTED = object()
_QUERY_ENV = ResultQueryEnvironment()


class ProjectedMcpResult(dict[str, Any]):
    """由平台产生的模型可见结果；远端 JSON 不会被反序列化为此类型。"""


class RestrictedMcpResult(dict[str, str]):
    """模型只收到状态，固定原因码仅由管理端单独读取。"""

    def __init__(self, reason: str):
        super().__init__(status="success", result_visibility="restricted")
        self.reason = reason


def restricted_model_result(reason: str = "fields_not_configured") -> RestrictedMcpResult:
    return RestrictedMcpResult(reason)


# 保留旧字段简写，RFC 路径交给标准解析器。
def _result_query(path: str) -> str:
    if path.startswith("$"):
        return path
    result = "$"
    for part in path.split("."):
        match = _FIELD_PART.fullmatch(part)
        if not match:
            raise ValueError(_SELECTOR_ERROR)
        name, array = match.groups()
        result += "[" + json.dumps(name) + "]"
        if array:
            result += "[*]"
    return result


def compile_result_fields(paths: Any) -> list[tuple[str, JSONPathQuery]]:
    if not isinstance(paths, (list, tuple)):
        raise ValueError("结果字段必须是路径列表")
    if len(paths) > MAX_RESULT_FIELDS:
        raise ValueError(f"最多配置 {MAX_RESULT_FIELDS} 个模型可见结果字段")
    queries = []
    legacy_paths = set()
    legacy_levels = {}
    for path in paths:
        if not isinstance(path, str) or len(path) > MAX_FIELD_PATH:
            raise ValueError("结果字段路径无效")
        try:
            query = _QUERY_ENV.compile(_result_query(path))
        except (JSONPathError, ValueError, RecursionError):
            raise ValueError(_SELECTOR_ERROR) from None
        if not path.startswith("$"):
            # 旧配置禁止将对象本身与其子字段混用，避免升级后扩大既有批准范围。
            if any(path.startswith(prefix + ".") or prefix.startswith(path + ".") for prefix in legacy_paths):
                raise ValueError("结果字段不能同时选择同一位置和它的子字段")
            legacy_paths.add(path)
            prefix = ()
            for segment in query.segments:
                selector = segment.selectors[0]
                kind = type(selector)
                if legacy_levels.setdefault(prefix, kind) is not kind:
                    raise ValueError("同一层需要统一使用对象属性或数组通配符")
                prefix += (selector.name if isinstance(selector, NameSelector) else None,)
        queries.append((path, query))
    return queries


def _reject_non_json_number(_value: str) -> None:
    raise ValueError("工具响应必须是有效 JSON")


def parse_mcp_json_result_text(text: str) -> Any:
    if len(text) > _MAX_RESPONSE_BYTES or len(text.encode()) > _MAX_RESPONSE_BYTES:
        raise ValueError("工具响应超出大小限制")
    return json.loads(text, parse_constant=_reject_non_json_number)


def _result_source(result: Any) -> Any:
    if isinstance(result, dict) and "structured_content" in result:
        return result["structured_content"]
    if isinstance(result, str):
        return parse_mcp_json_result_text(result)
    raise ValueError("工具响应没有 JSON 内容")


def _query_view(source: Any, budget: QueryBudget) -> Any:
    """保留所有键和值，仅为标准 JSONPath 求值计入资源预算。"""
    remaining = _MAX_RESPONSE_BYTES

    def copy(value: Any, depth: int = 0) -> Any:
        nonlocal remaining
        budget.spend()
        if depth > _MAX_DEPTH:
            raise QueryLimitExceeded
        remaining -= 1
        if isinstance(value, dict):
            result = QueryObject(budget)
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ValueError("JSON 对象键必须是字符串")
                remaining -= len(key.encode()) + 3
                if remaining < 0:
                    raise QueryLimitExceeded
                result[key] = copy(child, depth + 1)
            return result
        if isinstance(value, list):
            return QueryArray(budget, (copy(child, depth + 1) for child in value))
        if value is not None and type(value) not in (str, bool, int, float):
            raise ValueError("工具响应必须是有效 JSON")
        if isinstance(value, str):
            if len(value) > remaining:
                raise QueryLimitExceeded
            remaining -= len(value.encode())
        else:
            remaining -= len(json.dumps(value, allow_nan=False))
        if remaining < 0:
            raise QueryLimitExceeded
        return value

    return copy(source)


def _resolve_matches(
    query: JSONPathQuery, view: Any, budget: QueryBudget, *, legacy_empty_arrays: bool = False,
) -> Iterator[tuple[str | int, ...]]:
    for node in query.finditer(view):
        budget.spend()
        yield node.location
    # 旧简写保留空数组；RFC 路径严格按节点匹配，无匹配时不返回数据。
    if legacy_empty_arrays and all(
        len(segment.selectors) == 1
        and isinstance(segment, JSONPathChildSegment)
        and isinstance(segment.selectors[0], (NameSelector, IndexSelector, WildcardSelector))
        for segment in query.segments
    ):
        for index, segment in enumerate(query.segments):
            if not isinstance(segment.selectors[0], WildcardSelector):
                continue
            prefix = JSONPathQuery(env=query.env, segments=query.segments[:index])
            for node in prefix.finditer(view):
                budget.spend()
                if isinstance(node.value, list) and not node.value:
                    yield node.location


def _match_tree(
    source: Any, matches: dict[tuple[str | int, ...], None], budget: QueryBudget,
) -> Any:
    """按业务 JSON 的原结构返回已选节点，数组下标用 null 占位。"""
    tree = {}
    for location in sorted(matches, key=len):
        branch = tree
        for part in location:
            if _SELECTED in branch:
                break
            branch = branch.setdefault(part, {})
        else:
            branch[_SELECTED] = True

    def project(value, branch):
        budget.spend()
        if _SELECTED in branch:
            return value
        if isinstance(value, list):
            result = [None] * (max(branch) + 1)
            for index, child in branch.items():
                result[index] = project(value[index], child)
            return result
        result = {}
        for key, child in branch.items():
            result[key] = project(value[key], child)
        return result

    return project(source, tree)


def project_model_result(
    result: Any, paths: list[str] | tuple[str, ...], *, protected_values: tuple[Any, ...] = (),
    target_url: str = "", auth_headers: dict[str, str] | None = None,
) -> ProjectedMcpResult | RestrictedMcpResult:
    # 兼容旧调用签名；结果内容只由 paths 决定，认证信息不参与投影或过滤。
    if not paths:
        return restricted_model_result()
    try:
        queries = compile_result_fields(paths)
    except ValueError:
        return restricted_model_result("fields_invalid")
    try:
        source = _result_source(result)
    except (ValueError, TypeError, RecursionError):
        return restricted_model_result("invalid_response")
    budget = QueryBudget()
    try:
        view = _query_view(source, budget)
        matches: dict[tuple[str | int, ...], None] = {}
        for original, query in queries:
            # 旧字段简写把根数组映射为 items，RFC 表达式使用真实的根。
            legacy_items = isinstance(source, list) and (
                original.startswith("items.") or original.startswith("items[]") or original == "items"
            )
            if legacy_items:
                matches.update(dict.fromkeys(location[1:] for location in _resolve_matches(
                    query, {"items": view}, budget, legacy_empty_arrays=True,
                )))
            else:
                matches.update(dict.fromkeys(_resolve_matches(
                    query, view, budget, legacy_empty_arrays=not original.startswith("$"),
                )))
        if not matches:
            return restricted_model_result("no_matching_fields")
        data = _match_tree(source, matches, budget)
        if isinstance(data, list) and () not in matches:
            data = {"items": data}
        if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode()) > _MAX_RESULT_BYTES:
            return restricted_model_result("result_too_large")
        return ProjectedMcpResult(status="success", result_visibility="projected", data=data)
    except (QueryLimitExceeded, JSONPathRecursionError):
        return restricted_model_result("query_limit_exceeded")
    except (ValueError, TypeError, JSONPathError, RecursionError):
        return restricted_model_result("invalid_response")
