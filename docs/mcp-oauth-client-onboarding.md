# NanZi 平台 MCP · OAuth2 用户授权对接说明（临时版）

> **面向对象**：需要以「具体员工身份」调用 NanZi 平台 MCP 的 CRM / 门户 / 后台系统。
> **文档性质**：临时对接文档，内容随实现同步更新，可直接转发给对接方开发。
> **最后更新**：2026-11-09

---

## 0. 先解释一个最常见的困惑

对接时最容易卡住的两个变量是 `code` 和 `code_verifier`。它们**不是 NanZi 发给你的**，而是你自己的系统在授权过程中产生或收到的：

| 变量 | 谁产生 | 什么时候 | 存在哪 |
| --- | --- | --- | --- |
| `code_verifier` | **你的后端** | 跳转 NanZi 授权页**之前**，随机生成 | 你的会话 / Redis，浏览器 URL 里只出现它的 SHA256 摘要 |
| `code` | **NanZi** | 用户在授权页点「同意并继续」后，302 跳回你的回调地址时带上 | 你的后端从回调 query 里取，5 分钟内有效、只能用一次 |

NanZi 服务端全程拿不到 `code_verifier` 原文，只保存 `BASE64URL(SHA256(code_verifier))` 这个摘要（`code_challenge`），换 Token 时再让两边对上。这就是 PKCE 的作用：即使授权码被窃取，没有 verifier 也换不到 Token。

所以「复制一段 curl 直接跑」是跑不通的 —— 必须先完成浏览器里的授权跳转，才有 `code` 可用。

---

## 1. 动手前的四项准备

在 NanZi **MCP 服务台 → 外部 Client** 中创建或编辑一个 Client：

| 配置项 | 要求 | 常见踩坑 |
| --- | --- | --- |
| Client ID / Secret | 创建后复制保存 | Secret **只在创建或重置时明文显示一次**，丢了只能重置；重置后旧 Token 立即失效 |
| 授权模式 | 必须勾选 `authorization_code` | 只勾 `refresh_token` 会在授权页被拒（`unauthorized_client`） |
| 回调地址 | 填你系统的真实地址，**逐字符精确匹配**，不支持通配符 | 尾斜杠、端口、`http`/`https` 任意一处不同都会 `redirect_uri 未注册` |
| 允许 Scope | 覆盖你要用的能力 | 请求的 Scope 超出配置会 `invalid_scope` |

Scope 取值：`agent:list`、`agent:invoke`、`conversation:continue`、`knowledge:search`、`metadata:read`、`metadata:search`、`metadata:metrics:read`。

还要拿到两个地址：

- 授权地址：`<NanZi 域名>/oauth/authorize`
- MCP 端点 / Resource：`<NanZi 域名>/mcp/platform`（`resource` 参数必须与它完全一致）

---

## 2. 整条链路

```
你的后端                     浏览器                     NanZi
   │                           │                         │
   │ ①生成 verifier+state      │                         │
   │ ②302 到 /oauth/authorize  │                         │
   ├──────────────────────────>│ ③打开授权页（未登录先登录）│
   │                           ├────────────────────────>│
   │                           │ ④用户点「同意并继续」      │
   │                           │<────────────────────────┤
   │ ⑤回调 ?code=...&state=... │                         │
   │<──────────────────────────┤                         │
   │ ⑥校验 state               │                         │
   │ ⑦POST /oauth/token（Basic 认证 + code + verifier）   │
   ├─────────────────────────────────────────────────────>│
   │ ⑧access_token / refresh_token                        │
   │<─────────────────────────────────────────────────────┤
   │ ⑨POST /mcp/platform（只带 Bearer Token）             │
   ├─────────────────────────────────────────────────────>│
```

Token 有效期：授权码 **5 分钟**且一次性；Access Token **3600 秒**；Refresh Token **30 天**，每次刷新后轮换。

---

## 3. 完整可运行代码

保存为 `mcp_oauth_client.py`，依赖 `httpx`（`pip install httpx`）。

```python
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
    python mcp_oauth_client.py

依赖：httpx。
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
```

运行：

```bash
export NANZI_BASE_URL="https://<NanZi 域名>"
export NANZI_CLIENT_ID="mcp_xxx"
export NANZI_CLIENT_SECRET="mcp_secret_xxx"
export NANZI_REDIRECT_URI="http://localhost:8765/oauth/callback"   # 需先在服务台登记
export NANZI_SCOPES="agent:list agent:invoke"
python mcp_oauth_client.py
```

看到 `tools/list 返回 N 个工具` 就说明整条链路通了。

---

## 4. 四个关键步骤拆解

### 4.1 生成 PKCE 参数

```python
verifier = secrets.token_urlsafe(64)           # 43~128 字符，[A-Za-z0-9-._~]
challenge = base64.urlsafe_b64encode(
    hashlib.sha256(verifier.encode("ascii")).digest()
).rstrip(b"=").decode("ascii")                  # 去掉 = 填充
```

`verifier` 必须存进当前用户的登录会话；`challenge` 才进 URL。

### 4.2 发起授权

```text
GET <NanZi 域名>/oauth/authorize
  ?response_type=code
  &client_id=<client_id>
  &redirect_uri=<registered_redirect_uri>
  &scope=agent:invoke
  &state=<随机值，回调时校验>
  &code_challenge=<BASE64URL(SHA256(verifier))>
  &code_challenge_method=S256
  &resource=<NanZi 域名>/mcp/platform
```

- `code_challenge_method` 只支持 `S256`，不传或传 `plain` 会被拒；
- 用户未登录时，NanZi 会自动跳登录页，登录后回到授权页继续，**不需要你额外传凭证**；
- `resource` 必须是 `<NanZi 域名>/mcp/platform`，不一致会 `invalid_target`。

### 4.3 回调校验

回调形如 `GET <redirect_uri>?code=xxx&state=yyy`。两个动作缺一不可：

1. `state` 必须与你发起时保存的值**逐字相同**（用恒定时间比较），否则中止 —— 防 CSRF；
2. `code` 只能用一次、5 分钟过期，拿到后立即换 Token，不要缓存。

若用户点「拒绝」，回调是 `?error=access_denied`，没有 `code`。

### 4.4 换 Token 与调用

```text
POST <NanZi 域名>/oauth/token
Authorization: Basic base64(client_id:client_secret)
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=<callback_code>
&redirect_uri=<与授权时完全相同的值>
&code_verifier=<发起授权时保存的 verifier>
&resource=<NanZi 域名>/mcp/platform
```

响应：

```json
{
  "access_token": "opaque-token",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "opaque-refresh-token",
  "scope": "agent:invoke"
}
```

之后每次调用 MCP 都只带：

```text
POST <NanZi 域名>/mcp/platform
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: application/json, text/event-stream

{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

`Accept` 必须同时包含 `application/json` 和 `text/event-stream`：服务端可能以 SSE 返回，此时结果在 `data:` 行上（示例代码已处理）。

---

## 5. 接进你已有的 Web 框架

示例脚本自带本地回调服务器，只适合验证。真实对接要在你现有的 Web 服务里加两个路由。以 Flask 为例：

```python
import base64
import hashlib
import os
import secrets

import httpx
from flask import Flask, abort, redirect, request, session

app = Flask(__name__)
app.secret_key = os.environ["APP_SECRET_KEY"]     # 用于加密 session

BASE = os.environ["NANZI_BASE_URL"].rstrip("/")
CLIENT_ID = os.environ["NANZI_CLIENT_ID"]
CLIENT_SECRET = os.environ["NANZI_CLIENT_SECRET"]
REDIRECT_URI = os.environ["NANZI_REDIRECT_URI"]
RESOURCE = os.environ.get("NANZI_RESOURCE", f"{BASE}/mcp/platform")


@app.get("/nanzi/login")
def nanzi_login():
    """① 生成 PKCE + state 并存进会话，跳转授权页。"""
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(16)
    session["nanzi_verifier"] = verifier
    session["nanzi_state"] = state
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    query = httpx.QueryParams({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "agent:invoke",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": RESOURCE,
    })
    return redirect(f"{BASE}/oauth/authorize?{query}")


@app.get("/oauth/callback")
def nanzi_callback():
    """② 校验 state，换 Token，把结果写进自己的登录态。"""
    if request.args.get("error"):
        abort(400, request.args["error"])
    expected = session.pop("nanzi_state", None)
    if not expected or not secrets.compare_digest(request.args.get("state", ""), expected):
        abort(400, "state 不匹配")
    verifier = session.pop("nanzi_verifier", None)
    if not verifier:
        abort(400, "会话里没有 verifier，请重新发起授权")

    resp = httpx.post(
        f"{BASE}/oauth/token",
        auth=(CLIENT_ID, CLIENT_SECRET),
        data={
            "grant_type": "authorization_code",
            "code": request.args["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()
    # 生产环境：refresh_token 落库（加密），access_token 存服务端会话；
    # 不要把 refresh_token 放进 Cookie 或返回给浏览器。
    session["nanzi_access_token"] = token["access_token"]
    return redirect("/")
```

FastAPI 版把两个函数改成 `@app.get(...)` + `Depends` 取会话即可，逻辑一字不差；异步场景把 `httpx.post` 换成 `httpx.AsyncClient`。

**关键点**：`verifier` 和 `state` 存服务端会话，不要放 URL、Cookie 明文或前端 localStorage。

---

## 6. 排错对照表

| 报错 | HTTP | 原因 | 处理 |
| --- | --- | --- | --- |
| `invalid_client` | 401 | Client ID/Secret 不对；Client 已停用；Secret 被重置过 | 用最新 Secret；确认 Client 状态为启用 |
| `unauthorized_client` | 403 | Client 没勾 `authorization_code` | 编辑 Client 勾上该授权模式 |
| `invalid_request`：`redirect_uri 未注册` | 400 | 回调地址与登记值不完全一致（协议/域名/端口/路径/尾斜杠） | 逐字符比对，改一方对齐另一方 |
| `invalid_request`：`必须使用 PKCE S256` | 400 | 缺 `code_challenge` 或 method 不是 `S256` | 按 4.2 补参数 |
| `invalid_request`：`response_type、client_id、redirect_uri 是必填项` | 400 | 参数缺失 | 检查拼装 URL 的代码 |
| `invalid_scope` | 400 | 请求 Scope 超出 Client 允许范围 | 服务台补 Scope，或减少请求范围 |
| `invalid_target` | 400 | `resource` 不是 `<NanZi 域名>/mcp/platform` | 用规范地址，不要写 `mcp:nanzi-platform` |
| `access_denied` | 403 | 用户在授权页点了拒绝；或授权时登录态已失效 | 引导用户重新走授权 |
| `invalid_grant` | 400 | code 过期（>5 分钟）/ 已用过 / `redirect_uri` 与授权时不一致 / verifier 不匹配 | 重新完整走一次授权，不要复用旧 code |
| `unsupported_grant_type` | 400 | `grant_type` 不在 Client 允许范围 | 检查 Client 授权模式配置 |
| `invalid_client`（刷新时） | 401 | 同第一条 | 同第一条 |
| `401` / 无权限 | 401/403 | Access Token 过期、Client 被停用/删除、用户本身没有该资源权限 | 先刷新 Token，仍失败则重新授权；资源级权限需 NanZi 侧管理员授予 |

授权码**一次性**：换 Token 请求超时后重试，不能用同一个 code 重发，必须重新授权。

---

## 7. 安全红线

1. **Client Secret 只能出现在你后端的 Token 请求里**。不能放进浏览器、移动端、桌面客户端配置或代码仓库。
2. **不要在业务系统里保存 NanZi 用户的登录 Cookie 或用户 API Key**。第三方系统只通过授权码流程建立用户关联。
3. **`code_verifier` 与 `state` 只存服务端会话**，且授权完成后立即从会话里移除。
4. **`refresh_token` 落库时加密**，每个用户一条，不要与前端共享。
5. **Access Token 只用于 MCP 请求的 `Authorization: Bearer`**，不要当作用户身份凭证去调用 NanZi 其他 API。
6. **改配置等于吊销**：修改 Client 的 Scope、资源白名单、回调地址、授权模式或状态后，已签发的 Access Token、Refresh Token、授权关系和未消费授权码会立即失效，需要对应用户重新授权。

---

## 8. 接口速查

### 8.1 `GET /oauth/authorize`（浏览器跳转）

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `response_type` | 是 | 固定 `code` |
| `client_id` | 是 | 服务台复制 |
| `redirect_uri` | 是 | 与登记值完全一致 |
| `scope` | 否 | 空格分隔；省略时使用 Client 允许的全部 Scope |
| `state` | 否 | 强烈建议传，回调时校验 |
| `code_challenge` | 是 | `BASE64URL(SHA256(verifier))` |
| `code_challenge_method` | 是 | 固定 `S256` |
| `resource` | 否 | 建议显式传 `<NanZi 域名>/mcp/platform` |

成功：`302 <redirect_uri>?code=...&state=...`；拒绝：`302 <redirect_uri>?error=access_denied`。

### 8.2 `POST /oauth/token`

认证：`Authorization: Basic base64(client_id:client_secret)`（也支持表单里传 `client_id` / `client_secret`）。
编码：`application/x-www-form-urlencoded`。

| `grant_type` | 其它参数 |
| --- | --- |
| `authorization_code` | `code`、`redirect_uri`、`code_verifier`、`resource` |
| `refresh_token` | `refresh_token`、`resource` |

响应字段：`access_token`、`token_type`（`Bearer`）、`expires_in`（3600）、`refresh_token`（仅授权码换 Token 时返回）、`scope`。

### 8.3 `POST /oauth/revoke`

表单参数 `token`（Access Token 或 Refresh Token 均可），用同一个 Basic 认证。用户退出或解绑时调用，可立即失效。

### 8.4 发现端点（可选，供标准 OAuth 客户端自动配置）

- `GET <NanZi 域名>/.well-known/oauth-authorization-server`
- `GET <NanZi 域名>/.well-known/oauth-protected-resource`

---

## 9. 需要 NanZi 侧配合的事项

| 事项 | 找谁 |
| --- | --- |
| 创建 Client、配置回调地址与 Scope、重置 Secret | NanZi 平台管理员（MCP 服务台） |
| 给对应用户开通 Agent / 知识库 / 数据集的访问权限 | NanZi 平台管理员 |
| Token 签发异常、审计排查 | NanZi 平台管理员（按 Client ID + 时间点查审计日志） |

---

*本文档为临时对接资料，如与 NanZi 平台实际返回不一致，以平台返回的错误码与管理员确认为准。*
