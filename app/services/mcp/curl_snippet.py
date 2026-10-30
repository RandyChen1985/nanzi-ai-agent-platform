"""把「工具测试台」的一次调用整理成可直接复制执行的 curl 片段。

两个刻意的设计取舍：

* **用户断言只给占位符**。``X-Nanzi-User-Assertion`` 是按调用实时签发的 JWS
  （带 exp/nonce），任何静态值抄出去都会过期；这里能做的只是把位置写清楚。
* **两步而不是一步**。Streamable HTTP 必须先 ``initialize`` 拿到
  ``Mcp-Session-Id``，否则 ``tools/call`` 会被服务端拒绝。片段里把这两步和
  需要替换的值都标出来，比给一条「看着能用、跑起来 400」的命令有用。

真实令牌与私钥永远不出现在返回值里。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

PLACEHOLDER_TOKEN = "<该 MCP 服务配置的访问令牌>"
PLACEHOLDER_ASSERTION = "<平台实时签发的用户断言 JWS>"
PLACEHOLDER_SESSION = "<第 1 步响应头里的 Mcp-Session-Id>"
DEFAULT_ASSERTION_HEADER = "X-Nanzi-User-Assertion"


def _shell_quote(value: str) -> str:
    """POSIX 单引号转义：' -> '\\''。"""
    return "'" + str(value).replace("'", "'\\''") + "'"


def _auth_headers(server: Any) -> tuple[List[str], List[str]]:
    """返回 (curl 头行, 说明) —— 只含占位符，不含真实凭据。"""
    headers: List[str] = []
    notes: List[str] = []

    has_static_auth = bool(getattr(server, "auth_headers", None)) or str(
        getattr(server, "credential_mode", "") or ""
    ) in {"static", "fixed_token"}
    if has_static_auth:
        headers.append(f"  -H 'Authorization: Bearer {PLACEHOLDER_TOKEN}' \\")
        notes.append(
            "Authorization 里的令牌请从该 MCP 服务的配置中取（页面不会回显明文）。"
        )

    if bool(getattr(server, "user_assertion_enabled", False)):
        header_name = getattr(server, "user_assertion_header", None) or DEFAULT_ASSERTION_HEADER
        headers.append(f"  -H '{header_name}: {PLACEHOLDER_ASSERTION}' \\")
        notes.append(
            f"{header_name} 由平台按用户实时签发，值里含 exp 与随机数，"
            "不能用固定串复现；要发真实请求请用下方「运行测试」，它会在服务端完成签名。"
        )

    return headers, notes


def _jsonrpc_body(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def build_mcp_curl_snippet(
    server: Any,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成测试台用的 curl 片段（含 initialize 与 tools/call 两步）。"""
    url = str(getattr(server, "sse_url", "") or "")
    auth_lines, notes = _auth_headers(server)
    auth_block = ("\n".join(auth_lines) + "\n") if auth_lines else ""

    init_body = _jsonrpc_body(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nanzi-mcp-tester", "version": "1.0.0"},
            },
        }
    )
    call_body = _jsonrpc_body(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
    )

    common_headers = "  -H 'Content-Type: application/json' \\\n  -H 'Accept: application/json, text/event-stream' \\\n"

    curl = (
        "# 1) 建立会话：从响应头里取 Mcp-Session-Id\n"
        f"curl -sS -D - -o /dev/null -X POST {_shell_quote(url)} \\\n"
        f"{common_headers}"
        f"{auth_block}"
        f"  --data-raw {_shell_quote(init_body)}\n"
        "\n"
        "# 2) 调用工具：把上一步的 Mcp-Session-Id 填进去\n"
        f"curl -sS -X POST {_shell_quote(url)} \\\n"
        f"{common_headers}"
        f"  -H 'Mcp-Session-Id: {PLACEHOLDER_SESSION}' \\\n"
        f"{auth_block}"
        f"  --data-raw {_shell_quote(call_body)}\n"
    )

    notes = [
        "片段按 Streamable HTTP 生成：先 initialize 拿 Mcp-Session-Id，再 tools/call。"
        "纯 JSON-RPC 网关（如部分聚合平台）可跳过第 1 步。",
        *notes,
    ]

    return {
        "url": url,
        "tool_name": tool_name,
        "requires_session": True,
        "curl": curl,
        "notes": notes,
    }
