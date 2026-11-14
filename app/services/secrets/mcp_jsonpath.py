"""MCP JSONPath 求值及其资源边界。"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Iterator
from typing import Any

import regex
from iregexp_check import check
from jsonpath_rfc9535 import JSONPathEnvironment, JSONPathNode, JSONPathNodeList, Parser
from jsonpath_rfc9535.filter_expressions import FilterContext, RelativeFilterQuery
from jsonpath_rfc9535.function_extensions import ExpressionType, FilterFunction
from jsonpath_rfc9535.function_extensions._pattern import map_re


class QueryLimitExceeded(ValueError):
    """查询超过响应、遍历或正则处理预算。"""


class QueryBudget:
    def __init__(self) -> None:
        self.remaining = 100_000
        self.deadline = time.monotonic() + 0.25

    def spend(self, steps: int = 1) -> None:
        self.remaining -= steps
        if self.remaining < 0 or time.monotonic() >= self.deadline:
            raise QueryLimitExceeded


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict):
        return (
            isinstance(right, dict) and len(left) == len(right)
            and all(key in right and _json_equal(value, right[key]) for key, value in left.items())
        )
    if isinstance(left, list):
        return (
            isinstance(right, list) and len(left) == len(right)
            and all(_json_equal(first, second) for first, second in zip(left, right))
        )
    if isinstance(right, (dict, list)):
        return False
    return left == right


class QueryObject(dict):
    def __init__(self, budget: QueryBudget) -> None:
        super().__init__()
        self.budget = budget

    def __getitem__(self, key: str) -> Any:
        self.budget.spend()
        return super().__getitem__(key)

    def items(self) -> Iterator[tuple[str, Any]]:
        for key, value in super().items():
            self.budget.spend()
            yield key, value

    def __eq__(self, other):
        return _json_equal(self, other)


class QueryArray(list):
    def __init__(self, budget: QueryBudget, items: Iterable[Any]) -> None:
        super().__init__(items)
        self.budget = budget

    def __getitem__(self, key: int | slice) -> Any:
        self.budget.spend(len(range(*key.indices(len(self)))) + 1 if isinstance(key, slice) else 1)
        return super().__getitem__(key)

    def __iter__(self) -> Iterator[Any]:
        for value in super().__iter__():
            self.budget.spend()
            yield value

    def __eq__(self, other):
        return _json_equal(self, other)


class _RelativeQuery(RelativeFilterQuery):
    def evaluate(self, context: FilterContext) -> JSONPathNodeList:
        # 相对查询的当前节点仍是节点。嵌套过滤器中的 $ 继续指向原文档。
        nodes = [JSONPathNode(value=context.current, location=(), parent=None, root=context.root)]
        for segment in self.query.segments:
            nodes = segment.resolve(nodes)
        return JSONPathNodeList(nodes)


class _ResultParser(Parser):
    def parse_relative_query(self, stream):
        query = super().parse_relative_query(stream)
        return _RelativeQuery(token=query.token, query=query.query)


class _RegexFilter(FilterFunction):
    arg_types = [ExpressionType.VALUE, ExpressionType.VALUE]
    return_type = ExpressionType.LOGICAL

    def __init__(self, search: bool) -> None:
        self.match = regex.search if search else regex.fullmatch

    def __call__(self, value: object, pattern: object) -> bool:
        if not isinstance(value, str) or not isinstance(pattern, str):
            return False
        if len(pattern) > 2048:
            raise QueryLimitExceeded
        # regex 的匹配超时不覆盖编译。先限制量词展开，包括嵌套量词。
        expansion = len(pattern)
        for token in re.finditer(r"\\.|\[(?:\\.|[^\\\]])*\]|\{([0-9]+)(?:,([0-9]*))?\}", pattern):
            if token[1] is None:
                continue
            upper = token[2] or token[1]
            if len(upper) > 6:
                raise QueryLimitExceeded
            expansion *= max(1, int(upper))
            if expansion > 256_000:
                raise QueryLimitExceeded
        if not check(pattern):
            return False
        # I-Regexp 将方括号外未转义的 ^ 和 $ 作为普通字符。
        escaped_pattern = re.sub(
            r"\\.|\[(?:\\.|[^\\\]])*\]|[\^$]",
            lambda match: "\\" + match[0] if match[0] in {"^", "$"} else match[0],
            pattern,
        )
        try:
            return bool(self.match(map_re(escaped_pattern), value, regex.VERSION0, timeout=0.01))
        except regex.error:
            return False
        except TimeoutError:
            raise QueryLimitExceeded from None


class ResultQueryEnvironment(JSONPathEnvironment):
    parser_class = _ResultParser
    max_recursion_depth = 33

    def setup_function_extensions(self) -> None:
        super().setup_function_extensions()
        self.function_extensions["match"] = _RegexFilter(search=False)
        self.function_extensions["search"] = _RegexFilter(search=True)
