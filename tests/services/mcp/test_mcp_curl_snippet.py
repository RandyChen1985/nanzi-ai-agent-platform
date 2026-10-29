"""工具测试台的 curl 片段生成。

用户想「复制一条命令去自己调」，但这里有两个真实的约束：

1. **用户断言不能给静态值**：``X-Nanzi-User-Assertion`` 是按调用实时签发的 JWS，
   里面带 exp/nonce，抄一个固定串出来必然过期，只能给占位符 + 说明；
2. **Streamable HTTP 需要会话**：先 ``initialize`` 拿 ``Mcp-Session-Id``，直接
   ``tools/call`` 会被拒。所以片段是两步，而不是「一条命令搞定」。

片段里绝不能出现服务端配置的真实令牌或私钥——这个接口是只读展示用的。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.mcp.curl_snippet import build_mcp_curl_snippet

pytestmark = pytest.mark.no_infrastructure

URL = "http://localhost:8001/mcp/echo/3232d8b3-a10c-5354-83f2-ae1189919b0a/mcp"


def _server(**overrides):
    defaults = dict(
        sse_url=URL,
        credential_mode="static",
        auth_headers=json.dumps({"Authorization": "Bearer secret-token-value"}),
        user_assertion_enabled=False,
        user_assertion_header="X-Nanzi-User-Assertion",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_snippet_contains_two_step_curl_for_streamable_http():
    snippet = build_mcp_curl_snippet(_server(), "echo", {"message": "hi"})

    assert snippet["url"] == URL
    assert snippet["tool_name"] == "echo"
    curl = snippet["curl"]
    assert "initialize" in curl
    assert "tools/call" in curl
    assert 'Mcp-Session-Id' in curl
    # 两步各一个 JSON-RPC body；用 --data-raw 而不是 -d，避免 curl 改写 JSON
    assert curl.count("--data-raw") == 2


def test_snippet_inlines_tool_arguments_as_json():
    snippet = build_mcp_curl_snippet(_server(), "echo_slow", {"steps": 8, "interval_ms": 800})

    payload_line = next(
        line for line in snippet["curl"].splitlines() if "tools/call" in line
    )
    body = json.loads(payload_line.split("--data-raw ", 1)[1].strip("'"))
    assert body["params"]["name"] == "echo_slow"
    assert body["params"]["arguments"] == {"steps": 8, "interval_ms": 800}


def test_snippet_masks_static_token_instead_of_leaking_it():
    snippet = build_mcp_curl_snippet(_server(), "echo")

    assert "secret-token-value" not in snippet["curl"]
    for note in snippet["notes"]:
        assert "secret-token-value" not in note
    assert "Authorization: Bearer <" in snippet["curl"]


def test_snippet_adds_user_assertion_header_only_when_enabled():
    plain = build_mcp_curl_snippet(_server(), "echo")
    signed = build_mcp_curl_snippet(
        _server(user_assertion_enabled=True, user_assertion_header="X-Nanzi-User-Assertion"),
        "echo",
    )

    assert "X-Nanzi-User-Assertion" not in plain["curl"]
    assert "X-Nanzi-User-Assertion: <" in signed["curl"]
    # 断言值必须说明「实时签发」，否则用户会以为能直接抄
    assert any("实时签发" in note for note in signed["notes"])


def test_snippet_handles_server_without_static_auth():
    snippet = build_mcp_curl_snippet(
        _server(credential_mode="none", auth_headers=None),
        "echo",
    )

    assert "Authorization" not in snippet["curl"]
    assert "initialize" in snippet["curl"]


def test_snippet_quotes_tool_name_and_url_safely():
    """URL 或工具名里出现单引号时必须转义，否则复制出去就是坏命令。"""
    snippet = build_mcp_curl_snippet(
        _server(sse_url="http://localhost:8001/mcp/it's"),
        "weird'name",
    )

    assert "'\\''" in snippet["curl"]
