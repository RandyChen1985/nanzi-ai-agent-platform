"""工具结果的回显兜底。

模型看不到凭据、凭据只在出站那一刻补进请求——这两层已经封住了。但「工具把
收到的参数原样回显」不受我们控制：远端服务、某个调试接口，都可能带着值一起
回到上下文。这里在回灌前做一次精确匹配。

边界同样重要：**扫不到正常内容**。值太短就不登记，宁可漏掉极短的，
也不要把结果搅乱。
"""

from __future__ import annotations

import pytest

from app.services.secrets.redact import (
    MAX_VALUES,
    MIN_LENGTH,
    injected_count,
    looks_like_leak,
    redact_text,
    redact_value,
    register_injected,
    reset_injected,
)


pytestmark = pytest.mark.no_infrastructure

TOKEN = "sk-live-abcdefghijklmnop"


@pytest.fixture(autouse=True)
def _clean():
    reset_injected()
    yield
    reset_injected()


def test_registered_value_is_masked():
    register_injected(TOKEN)

    out = redact_text(f"远端回显：{TOKEN}")

    assert TOKEN not in out
    assert "sk-l***mnop" in out


def test_unrelated_text_is_untouched():
    register_injected(TOKEN)

    text = "这是一段完全无关的正常返回内容"
    assert redact_text(text) == text


def test_short_values_are_not_registered():
    """"1" / "true" 这种值在任何文本里都能命中，登记了等于到处打码。"""
    register_injected("1")
    register_injected("true")
    register_injected("x" * (MIN_LENGTH - 1))

    assert injected_count() == 0
    assert redact_text("1 true xxxxxxx") == "1 true xxxxxxx"


def test_value_at_threshold_is_registered():
    value = "y" * MIN_LENGTH
    register_injected(value)
    assert injected_count() == 1


def test_duplicate_registration_is_ignored():
    register_injected(TOKEN)
    register_injected(TOKEN)
    assert injected_count() == 1


def test_value_count_is_capped():
    """一次请求可能连调很多工具；不设上限扫描会线性变慢。"""
    for i in range(MAX_VALUES + 5):
        register_injected(f"secret-value-{i:04d}")
    assert injected_count() == MAX_VALUES


def test_nested_structures_are_redacted():
    register_injected(TOKEN)

    payload = {
        "success": True,
        "content": [{"type": "text", "text": f"echo {TOKEN}"}],
        "meta": {"note": TOKEN},
    }
    out = redact_value(payload)

    assert TOKEN not in str(out)
    assert out["success"] is True
    # key 不动：字段名不是值
    assert "note" in out["meta"]


def test_tuples_keep_their_type():
    register_injected(TOKEN)
    out = redact_value((TOKEN, "ok"))
    assert isinstance(out, tuple)
    assert TOKEN not in out[0]


def test_non_string_values_pass_through():
    register_injected(TOKEN)
    assert redact_value(42) == 42
    assert redact_value(None) is None
    assert redact_value(True) is True


def test_nothing_registered_means_no_work():
    """绝大多数调用没有注入凭据，这条路径必须便宜。"""
    assert redact_text(TOKEN) == TOKEN
    assert looks_like_leak(TOKEN) is False


def test_looks_like_leak_is_a_diagnostic():
    register_injected(TOKEN)
    assert looks_like_leak(f"x {TOKEN} y") is True
    assert looks_like_leak("nothing here") is False
