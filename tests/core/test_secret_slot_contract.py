"""槽位前缀必须和注入层真正读取的前缀一致。

`bind_secret` 以前只校验「有前缀、有名字」，于是可以绑成 `foo:bar` —— 绑定记录
写进去了，`GET /bindings` 也会理直气壮地显示「已绑定」，但运行时永远读不到它。
用户以为配好了，调用时才发现没生效，而且没有任何报错。
"""

import pytest

from app.services.secrets.center import ALLOWED_SLOT_PREFIXES
from app.services.secrets.policy import resolve_api_tool_slot


def test_mcp_accepts_header_and_arg_slots():
    """header 走 `apply_mcp_bindings`，arg 走 `apply_mcp_arg_bindings`。

    `sse:` 之类绑了没人读，不能放进来——那会让绑定记录写进去、面板显示「已绑定」，
    运行时却永远读不到。
    """
    assert ALLOWED_SLOT_PREFIXES["mcp_server"] == frozenset({"header", "arg"})


def test_api_tool_accepts_header_path_query_body():
    """四种都在注入层有对应实现；path 是 URL 占位 `{param}`。"""
    assert ALLOWED_SLOT_PREFIXES["api_tool"] == frozenset(
        {"header", "path", "query", "body"}
    )


def test_script_only_accepts_env_slots():
    assert ALLOWED_SLOT_PREFIXES["script_tool"] == frozenset({"env"})


def test_every_consumer_type_is_covered():
    from app.services.secrets.center import VALID_CONSUMER_TYPES

    assert set(ALLOWED_SLOT_PREFIXES) == set(VALID_CONSUMER_TYPES)


def test_every_allowed_prefix_has_an_injection_implementation():
    """反过来也查一遍：表里允许的前缀，注入层得真有人产出它。

    API 工具的槽位现在由 `policy.resolve_api_tool_slot()` 推（前端只传参数名），
    所以字面量可能落在 policy 而不是 injection —— 两处都算。
    """
    import inspect

    from app.services.secrets import injection, policy

    source = inspect.getsource(injection) + inspect.getsource(policy)
    for prefixes in ALLOWED_SLOT_PREFIXES.values():
        for prefix in prefixes:
            assert (
                f'"{prefix}:' in source
                or f"'{prefix}:'" in source
                or f'"{prefix}"' in source
                or f"'{prefix}'" in source
            ), prefix


@pytest.mark.parametrize(
    ("consumer_type", "slot", "ok"),
    [
        ("mcp_server", "header:Authorization", True),
        ("mcp_server", "arg:query-customer:api_key", True),
        ("mcp_server", "query:token", False),
        ("mcp_server", "env:TOKEN", False),
        ("api_tool", "header:Authorization", True),
        ("api_tool", "query:api_key", True),
        ("api_tool", "body:tenant", True),
        ("api_tool", "path:applicant", True),
        ("api_tool", "env:TOKEN", False),
        ("script_tool", "env:DB_PASSWORD", True),
        ("script_tool", "header:Authorization", False),
    ],
)
def test_slot_prefix_matrix(consumer_type, slot, ok):
    prefix, _, name = slot.partition(":")
    assert name
    assert (prefix in ALLOWED_SLOT_PREFIXES[consumer_type]) is ok


class TestResolveApiToolSlot:
    """前端只传参数名，槽位由后端按工具配置推。

    判断顺序不能反：先看 URL 占位，再看 method。`POST /refund/{applicant}`
    里的 applicant 是 path 参数，只看 method 会绑到 body 上——记录写进去了、
    面板显示「已绑定」、运行时永远匹配不到。
    """

    def test_url_placeholder_wins_over_method(self):
        assert (
            resolve_api_tool_slot(
                name="applicant",
                method="POST",
                url_template="https://api.test/refund/{applicant}",
            )
            == "path:applicant"
        )

    def test_get_falls_back_to_query(self):
        assert (
            resolve_api_tool_slot(
                name="page", method="GET", url_template="https://api.test/list"
            )
            == "query:page"
        )

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_other_methods_fall_back_to_body(self, method):
        assert (
            resolve_api_tool_slot(
                name="tenant", method=method, url_template="https://api.test/x"
            )
            == f"body:tenant"
        )

    def test_missing_method_defaults_to_get(self):
        """method 缺省按 GET —— 和注入层 ``(method or "GET")`` 的口径一致。"""
        assert resolve_api_tool_slot(name="page", method=None) == "query:page"

    def test_blank_name_is_empty(self):
        assert resolve_api_tool_slot(name="   ", method="GET") == ""

    def test_partial_placeholder_does_not_match(self):
        """`{applicant_id}` 里的 applicant 不是占位符，别误判成 path。"""
        assert (
            resolve_api_tool_slot(
                name="applicant",
                method="GET",
                url_template="https://api.test/x/{applicant_id}",
            )
            == "query:applicant"
        )
