#!/usr/bin/env python3
"""NanZi Platform MCP 的 Authorization Code + PKCE 完整示例。

脚本把整条用户授权链路一次性跑完，用来验证一个外部 Client 是否配置正确：

1. 后端生成 PKCE ``code_verifier`` / ``code_challenge`` 和 ``state``；
2. 浏览器打开 NanZi 授权页，用户登录并点「同意并继续」；
3. NanZi 302 跳回本机回调地址，脚本从 query 里取出一次性 ``code``；
4. 脚本用 ``client_id + client_secret + code_verifier`` 换取 Access Token；
5. 用 Access Token 调 MCP ``tools/list``，确认 Token 确实能代表该用户调用。

两个容易搞混的变量就来自这里：

- ``PKCE_CODE_VERIFIER``：第 1 步由业务后端自己生成并暂存的随机串，NanZi
  只保存它的 SHA256 ``code_challenge``，永远拿不到原文；
- ``CALLBACK_CODE``：第 3 步 NanZi 授权页同意后 302 带回来的 ``code``。

运行前提
--------
1. MCP 服务台里该 Client 已勾选 ``authorization_code`` 授权模式；
2. 登记的回调地址与 ``NANZI_REDIRECT_URI`` 完全一致（不支持通配符）；
3. Client 允许的 Scope 覆盖 ``NANZI_SCOPES``。

用法
----
    export NANZI_BASE_URL="http://localhost:8001"
    export NANZI_CLIENT_ID="mcp_xxx"
    export NANZI_CLIENT_SECRET="mcp_secret_xxx"
    export NANZI_REDIRECT_URI="http://localhost:8765/oauth/callback"
    python scripts/examples/mcp_oauth_client_example.py

依赖：httpx（项目已有）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


BASE_URL = os.environ.get("NANZI_BASE_URL", "http://localhost:8001").rstrip("/")
CLIENT_ID = os.environ.get("NANZI_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("NANZI_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("NANZI_REDIRECT_URI", "http://localhost:8765/oauth/callback")
# 必须是 base + /mcp/platform 这个规范地址，写成别的值会被以 invalid_target 拒绝。
RESOURCE = os.environ.get("NANZI_RESOURCE", f"{BASE_URL}/mcp/platform")
SCOPES = os.environ.get("NANZI_SCOPES", "agent:list agent:invoke metadata:read").split()
CALLBACK_TIMEOUT_SECONDS = 300


class _CallbackHandler(BaseHTTPRequestHandler):
    """只处理一次 OAuth 回调的最小 HTTP handler。"""

    expected_state: str = ""
    callback_path: str = "/oauth/callback"
    result: dict[str, str] = {}
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != self.callback_path:
            self._reply(404, "未找到该路径")
            return

        params = urllib.parse.parse_qs(parsed.query)
        error = params.get("error", [""])[0]
        code = params.get("code", [""])[0]
        state = params.get("state", [""])[0]

        if error:
            ok, message = False, f"用户在授权页拒绝了授权：{error}"
        elif not secrets.compare_digest(state, self.expected_state):
            # state 不匹配说明这不是本次发起的授权，必须中止，避免 CSRF。
            ok, message = False, "state 不匹配，已中止本次授权"
        elif not code:
            ok, message = False, "回调里没有 code 参数"
        else:
            ok, message = True, "授权完成，可以回到终端继续"

        type(self).result = {"ok": ok, "message": message, "code": code}
        self._reply(200 if ok else 400, message)
        type(self).done.set()

    def _reply(self, status: int, message: str) -> None:
        body = f"<!doctype html><meta charset=utf-8><p>{message}</p>".encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """屏蔽 BaseHTTPRequestHandler 往 stderr 刷的访问日志。"""


def build_challenge(verifier: str) -> str:
    """按 RFC 7636 的 S256 计算 challenge：BASE64URL(SHA256(verifier))。"""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_pkce_pair() -> tuple[str, str]:
    """生成 (code_verifier, code_challenge)。"""
    verifier = secrets.token_urlsafe(64)
    return verifier, build_challenge(verifier)


def build_authorize_url(verifier: str, state: str) -> str:
    """拼出要交给浏览器的授权地址；verifier 只留在后端，URL 里只有 challenge。"""
    challenge = build_challenge(verifier)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": RESOURCE,
        }
    )
    return f"{BASE_URL}/oauth/authorize?{query}"


def wait_for_callback(verifier: str, state: str) -> str:
    """起一个本地回调服务器，等用户授权完成后返回一次性 code。"""
    parsed = urllib.parse.urlsplit(REDIRECT_URI)
    if not parsed.port:
        raise SystemExit("NANZI_REDIRECT_URI 必须带端口，例如 http://localhost:8765/oauth/callback")

    _CallbackHandler.expected_state = state
    _CallbackHandler.callback_path = parsed.path or "/oauth/callback"
    _CallbackHandler.result = {}
    _CallbackHandler.done = threading.Event()

    server = ThreadingHTTPServer((parsed.hostname or "127.0.0.1", parsed.port), _CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        authorize_url = build_authorize_url(verifier, state)
        print("\n请在弹出的浏览器里登录 NanZi 并点击「同意并继续」。")
        print("若浏览器没有自动打开，请手动访问：\n")
        print(authorize_url + "\n")
        webbrowser.open(authorize_url)

        if not _CallbackHandler.done.wait(timeout=CALLBACK_TIMEOUT_SECONDS):
            raise SystemExit(f"等待授权回调超时（{CALLBACK_TIMEOUT_SECONDS} 秒）")
    finally:
        server.shutdown()
        server.server_close()

    result = _CallbackHandler.result
    if not result.get("ok"):
        raise SystemExit(result.get("message") or "授权失败")
    return result["code"]


def exchange_code(code: str, verifier: str) -> dict:
    """用回调 code + verifier + Client Secret 换绑定该用户的 Access Token。"""
    response = httpx.post(
        f"{BASE_URL}/oauth/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise SystemExit(f"换 Token 失败：HTTP {response.status_code} {response.text}")
    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Access Token 到期后用 Refresh Token 续期，用户身份不变。"""
    response = httpx.post(
        f"{BASE_URL}/oauth/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "resource": RESOURCE,
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise SystemExit(f"刷新 Token 失败：HTTP {response.status_code} {response.text}")
    return response.json()


def call_mcp_tools_list(access_token: str) -> dict:
    """携带 Bearer Token 调 MCP tools/list，验证 Token 真的可用。"""
    response = httpx.post(
        RESOURCE,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            # Streamable HTTP 传输可能回 SSE，两种都要声明支持。
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(f"调用 MCP 失败：HTTP {response.status_code} {response.text}")
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        # SSE 响应里真正的 JSON-RPC 结果在 data: 行上。
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return httpx.Response(200, content=line[5:].strip().encode()).json()
        raise SystemExit("SSE 响应里没有 data 行")
    return response.json()


def main() -> int:
    missing = [
        name
        for name, value in (
            ("NANZI_CLIENT_ID", CLIENT_ID),
            ("NANZI_CLIENT_SECRET", CLIENT_SECRET),
        )
        if not value
    ]
    if missing:
        print(f"缺少环境变量：{', '.join(missing)}", file=sys.stderr)
        return 2

    verifier, _challenge = new_pkce_pair()
    state = secrets.token_urlsafe(16)

    code = wait_for_callback(verifier, state)
    print(f"已收到授权码（code 长度 {len(code)}），正在换取 Access Token……")

    token = exchange_code(code, verifier)
    access_token = token["access_token"]
    print(f"Access Token 有效期 {token.get('expires_in')} 秒，Scope：{token.get('scope')}")

    result = call_mcp_tools_list(access_token)
    tools = (result.get("result") or {}).get("tools") or []
    print(f"\ntools/list 返回 {len(tools)} 个工具，前几个是：")
    for tool in tools[:10]:
        print(f"  - {tool.get('name')}")

    if token.get("refresh_token"):
        print(
            "\n已获得 Refresh Token（30 天有效，只能存在业务方后端）；"
            "到期后可调用 refresh_access_token() 续期，用户身份不变。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
