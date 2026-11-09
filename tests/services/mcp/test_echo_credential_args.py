"""Echo 的凭据参数：给「参数级注入」一条能自己跑通的验收路径。

外部 MCP 大多不会为了验收入参而回显 —— 要么根本没这个字段，要么回显明文
（那更不能用来测凭据）。Echo 是平台自己的，所以这里回显**掩码 + 长度**：
能看到「注入了、多长」，看不到明文。
"""

from __future__ import annotations

import json

import pytest

from app.services.mcp.echo_server import _describe_arguments, echo_tool_schema


pytestmark = pytest.mark.no_infrastructure


def test_schema_declares_the_three_probe_params():
    schema = json.loads(echo_tool_schema())

    assert set(schema["properties"]) == {"api_key", "tenant_token", "note"}
    # 全都是可选的：标成「来自凭据」后模型不传也不会触发校验错误
    assert "required" not in schema or not schema["required"]


def test_received_argument_is_masked_not_echoed():
    described = _describe_arguments(
        api_key="sk-live-abcdefghijklmnop",
        tenant_token="tenant-001-xyz",
        note="模型自己填的",
    )

    # 明文一个都不许回显——这个工具会在对话里直接把结果摊给用户看
    assert "sk-live-abcdefghijklmnop" not in json.dumps(described, ensure_ascii=False)
    assert "tenant-001-xyz" not in json.dumps(described, ensure_ascii=False)

    assert described["api_key"]["received"] is True
    assert described["api_key"]["length"] == 24
    assert described["api_key"]["masked"]


def test_missing_injection_is_visible_as_not_received():
    """没注入时 ``received: False``——一眼看出是「平台没给」而不是「给错了」。"""
    described = _describe_arguments(api_key=None, tenant_token=None, note="hi")

    assert described["api_key"] == {"received": False}
    assert described["tenant_token"] == {"received": False}
    # 普通参数原样带出，方便对比：只有凭据参数是平台补的
    assert described["note"] == "hi"


def test_empty_string_counts_as_not_received():
    """空串和没传是同一件事：远端拿到的都是「没有这个值」。"""
    assert _describe_arguments(api_key="", tenant_token="", note=None)["api_key"] == {
        "received": False
    }
