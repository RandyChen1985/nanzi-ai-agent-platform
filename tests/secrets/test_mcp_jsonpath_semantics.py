import pytest

from app.services.secrets import mcp_jsonpath, mcp_model_result
from app.services.secrets.mcp_model_result import project_model_result

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize("condition", ["@", "count(@) == 1"])
def test_current_node_preserves_false_zero_and_empty_values(condition):
    values = [False, 0, "", None, [], {}]

    result = project_model_result(
        {"structured_content": {"values": values}},
        [f"$.values[?{condition}]"],
        protected_values=(),
    )

    assert result["data"] == {"values": [False, 0, "", None, [], {}]}


def test_nested_relative_filter_uses_original_document_root():
    source = {
        "threshold": 3,
        "groups": [
            {"label": "selected", "scores": [1, 4]},
            {"label": "skipped", "scores": [2, 3]},
        ],
    }

    result = project_model_result(
        {"structured_content": source},
        ["$.groups[?@.scores[?@ > $.threshold]].label"],
        protected_values=(),
    )

    assert result["data"] == {"groups": [{"label": "selected"}]}


@pytest.mark.parametrize(("operator", "expected"), [
    ("==", [None, {"label": "equal"}]),
    ("!=", [{"label": "different"}]),
    ("<=", [None, {"label": "equal"}]),
    (">=", [None, {"label": "equal"}]),
])
def test_structural_comparison_keeps_boolean_and_numeric_values_distinct(operator, expected):
    entries = [
        {"label": "different", "left": {"rows": [{"n": False}]}, "right": {"rows": [{"n": 0}]}},
        {"label": "equal", "left": {"rows": [{"n": 1}]}, "right": {"rows": [{"n": 1.0}]}},
    ]

    result = project_model_result(
        {"structured_content": {"entries": entries}},
        [f"$.entries[?@.left {operator} @.right].label"],
        protected_values=(),
    )

    assert result["data"] == {"entries": expected}


# I-Regexp 的 ^ 和 $ 是普通字符，CTS 的两项锚点预期见上游问题。
# https://github.com/jsonpath-standard/jsonpath-compliance-test-suite/issues/122
@pytest.mark.parametrize(("condition", "values", "expected"), [
    ("match(@, '^ab.*')", ["abc", "^abc"], [None, "^abc"]),
    ("search(@, 'ab$')", ["ab", "inside ab$ suffix"], [None, "inside ab$ suffix"]),
    ("search(@, '[a&&b]')", ["a", "b", "&"], ["a", "b", "&"]),
])
def test_iregexp_literals_keep_their_standard_meaning(condition, values, expected):
    result = project_model_result(
        {"structured_content": {"values": values}},
        [f"$.values[?{condition}]"],
        protected_values=(),
    )

    assert result["data"] == {"values": expected}


@pytest.mark.parametrize(("function", "operation"), [
    ("match", "fullmatch"),
    ("search", "search"),
])
def test_regex_timeout_restricts_entire_projection(monkeypatch, function, operation):
    def expire(_pattern, _value, _flags, *, timeout):
        assert 0 < timeout < 1
        raise TimeoutError

    monkeypatch.setattr(mcp_jsonpath.regex, operation, expire)
    monkeypatch.setattr(mcp_model_result, "_QUERY_ENV", mcp_jsonpath.ResultQueryEnvironment())

    result = project_model_result(
        {"structured_content": {"summary": "ready", "values": ["Alice"]}},
        ["$.summary", f"$.values[?{function}(@, 'Alice')]"],
        protected_values=(),
    )

    assert result == {"status": "success", "result_visibility": "restricted"}
    assert result.reason == "query_limit_exceeded"


def test_nonmatching_filter_still_spends_traversal_budget(monkeypatch):
    class SmallStepBudget(mcp_jsonpath.QueryBudget):
        def __init__(self):
            super().__init__()
            self.remaining = 128
            self.deadline = float("inf")

    monkeypatch.setattr(mcp_model_result, "QueryBudget", SmallStepBudget)
    response = {"structured_content": {
        "summary": "ready",
        "entries": [{"status": "ready"} for _ in range(50)],
    }}

    control = project_model_result(response, ["$.summary"], protected_values=())
    result = project_model_result(
        response,
        ["$.summary", "$.entries[?@.status == 'pending']"],
        protected_values=(),
    )

    assert control["data"] == {"summary": "ready"}
    assert result == {"status": "success", "result_visibility": "restricted"}
    assert result.reason == "query_limit_exceeded"
