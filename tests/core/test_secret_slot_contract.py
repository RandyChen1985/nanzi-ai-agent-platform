"""槽位前缀必须和注入层真正读取的前缀一致。

`bind_secret` 以前只校验「有前缀、有名字」，于是可以绑成 `foo:bar` —— 绑定记录
写进去了，`GET /bindings` 也会理直气壮地显示「已绑定」，但运行时永远读不到它。
用户以为配好了，调用时才发现没生效，而且没有任何报错。
"""

import pytest

from app.services.secrets.center import ALLOWED_SLOT_PREFIXES


def test_mcp_only_accepts_header_slots():
    """`apply_mcp_bindings` 只遍历 headers，`sse:` 之类绑了也没人读。"""
    assert ALLOWED_SLOT_PREFIXES["mcp_server"] == frozenset({"header"})


def test_api_tool_accepts_header_query_body():
    """header / query / body 三种都在注入层有对应实现。"""
    assert ALLOWED_SLOT_PREFIXES["api_tool"] == frozenset({"header", "query", "body"})


def test_script_only_accepts_env_slots():
    assert ALLOWED_SLOT_PREFIXES["script_tool"] == frozenset({"env"})


def test_every_consumer_type_is_covered():
    from app.services.secrets.center import VALID_CONSUMER_TYPES

    assert set(ALLOWED_SLOT_PREFIXES) == set(VALID_CONSUMER_TYPES)


def test_every_allowed_prefix_has_an_injection_implementation():
    """反过来也查一遍：表里允许的前缀，注入层得真有人读它。"""
    import inspect

    from app.services.secrets import injection

    source = inspect.getsource(injection)
    for prefixes in ALLOWED_SLOT_PREFIXES.values():
        for prefix in prefixes:
            assert f'"{prefix}:"' in source or f"'{prefix}:'" in source or f"{prefix}:" in source


@pytest.mark.parametrize(
    ("consumer_type", "slot", "ok"),
    [
        ("mcp_server", "header:Authorization", True),
        ("mcp_server", "query:token", False),
        ("api_tool", "header:Authorization", True),
        ("api_tool", "query:api_key", True),
        ("api_tool", "body:tenant", True),
        ("api_tool", "env:TOKEN", False),
        ("script_tool", "env:DB_PASSWORD", True),
        ("script_tool", "header:Authorization", False),
    ],
)
def test_slot_prefix_matrix(consumer_type, slot, ok):
    prefix, _, name = slot.partition(":")
    assert name
    assert (prefix in ALLOWED_SLOT_PREFIXES[consumer_type]) is ok
