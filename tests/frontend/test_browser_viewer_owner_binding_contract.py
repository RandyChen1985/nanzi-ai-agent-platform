"""浏览器实时画面（viewer WebSocket）身份绑定的前端契约。

后端已把 viewer 连接收紧为「只有会话归属人能连」（见
`tests/api/v1/test_browser_viewer_owner_binding.py`）。但服务端要判归属，就得先**拿到
连接者身份**：浏览器发起的 WebSocket 握手不能自定义请求头，跨站第三方 iframe 下
`SameSite=Lax` 的 `portal_session` / `embed_session` 也不会随握手发送，因此前端必须把
父级已有的凭据（EmbedChat 的 `config.token`，面板里已经在给 HTTP 请求用）通过
`browser-auth.<credential>` 子协议带上去——这是唯一「不经 URL、又能带任意值」的通道。

本文件锁定：
1. 面板确实带上身份子协议，且凭据只取父级传入的 `authToken`（不从 localStorage 兜底）；
2. 子协议值经过白名单校验——浏览器对非法 token 字符会在 `new WebSocket()` 直接抛
   `SyntaxError`，宁可退回 Cookie 身份也不能让面板连不上；
3. 凭据变化（换用户/换会话令牌）会触发重连，否则会拿着旧身份复用旧连接；
4. 后端「先判归属、再 accept」的顺序不能被改回去。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_browser_panel_sends_the_identity_subprotocol_from_the_parent_token():
    source = _source("frontend/src/components/embed/BrowserPanel.vue")
    assert "const VIEWER_AUTH_PROTOCOL_PREFIX = 'browser-auth.';" in source
    assert "new WebSocket(url, viewerSocketProtocols())" in source
    connect = source.index("const viewerSocketProtocols = ")
    body = source[connect:source.index("\n};", connect)]
    assert "browser-viewer." in body
    assert "VIEWER_AUTH_PROTOCOL_PREFIX" in body
    assert "props.authToken" in body
    # 凭据只从父级传入：面板不得自行去 localStorage/sessionStorage 里找令牌。
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_browser_panel_guards_the_subprotocol_charset():
    """子协议值必须是合法 HTTP token 字符，否则 `new WebSocket()` 直接抛错。"""
    source = _source("frontend/src/components/embed/BrowserPanel.vue")
    assert "const isSubprotocolSafeCredential = (value?: string | null): boolean =>" in source
    assert "/^[A-Za-z0-9._~-]+$/.test(value)" in source
    # 判定函数必须被真正用于拼接：不安全就只发 viewer token 协议（退回 Cookie 身份）。
    protocols = source.index("const viewerSocketProtocols = ")
    body = source[protocols:source.index("\n};", protocols)]
    assert "isSubprotocolSafeCredential(props.authToken)" in body


def test_browser_panel_reconnects_when_the_identity_credential_changes():
    source = _source("frontend/src/components/embed/BrowserPanel.vue")
    start = source.index("watch(\n  () => [props.visible, props.sessionId, props.viewerToken")
    watch = source[start:source.index(");", start)]
    assert "props.authToken" in watch


def test_embed_chat_passes_its_credential_to_the_browser_panel():
    source = _source("frontend/src/views/EmbedChat.vue")
    start = source.index("<BrowserPanel")
    panel = source[start:source.index("/>", start)]
    assert ':auth-token="config.token"' in panel
    assert ':viewer-token="browserViewerToken"' in panel


def test_browser_viewer_binds_identity_before_accepting_the_socket():
    """安全顺序：取不到身份、或身份不是归属人时必须先 close(4403)，绝不能先 accept 再判。"""
    source = _source("app/api/v1/endpoints/browser.py")
    assert '_VIEWER_AUTH_PROTOCOL_PREFIX = "browser-auth."' in source
    handler = source.index("async def browser_viewer(")
    body = source[handler:]
    resolve = body.index("viewer_user_id = await _resolve_viewer_user_id(websocket, db)")
    accept = body.index("await websocket.accept(")
    assert resolve < accept
    guard = body.index("viewer_user_id != str(session.user_id)")
    assert guard < accept
    assert body.index("await websocket.close(code=4403)") < accept
    # 归属判定对所有角色一致：不得出现 admin 例外分支。
    assert "role" not in body[:accept]


def test_browser_viewer_identity_resolution_has_no_admin_escape_hatch():
    source = _source("app/api/v1/endpoints/browser.py")
    start = source.index("async def _resolve_viewer_user_id(")
    helper = source[start:source.index("\ndef _viewer_origin_allowed", start)]
    assert 'websocket.cookies.get("portal_session") or websocket.cookies.get("embed_session")' in helper
    assert "verify_api_key" in helper
    assert "admin" not in helper
