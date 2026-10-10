"""浏览器实时画面（viewer WebSocket）必须只对**会话归属人**开放。

## 背景

viewer token 是**会话级 bearer 凭证**：`BrowserSessionService.resolve_viewer_token` 只校验
「过期 + 令牌哈希」，WebSocket 入口只用它比对 `session.id != session_id`，因此**任何拿到该
令牌的人**都能连上这条连接——而这条连接不只是看画面，还能转发鼠标/键盘/导航事件。令牌的
泄露路径是客户端状态继承（同一浏览器/标签页换了使用者，上一份组件状态里的 token 还在），
前端已做到「身份变化即断开面板并丢弃 token」，但那只是堵住继承路径，凭证本身仍是万能钥匙。

本文件锁定服务端侧的归属绑定：连接者必须能证明自己就是该浏览器会话的归属人。

## 约定

- 身份来源两条路：
  1. **显式凭据**：WebSocket 子协议 `browser-auth.<credential>`——跨站第三方 iframe 下
     `portal_session` / `embed_session` 都是 `SameSite=Lax`，握手不会带 Cookie，必须有这条路；
  2. **同源 Cookie**：`portal_session` 优先，其次 `embed_session`（与 `require_api_key` 同序）。
- 取不到身份、或身份与会话归属人不一致 → `close(4403)`，且**不得** `accept()`、不得触碰
  `browser_runtime`（否则等于先连上再拒绝，画面已经发出去了）。
- 归属判定对所有角色一致：admin 也不能接管他人的浏览器会话。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints.browser import (
    _resolve_viewer_user_id,
    _viewer_credential_from_websocket,
    browser_viewer,
)

pytestmark = pytest.mark.no_infrastructure

_VIEWER_TOKEN = "1750000000.abcdef0123456789"
_OWNER_USER_ID = "101"
_OTHER_USER_ID = "202"


class _FakeWebSocket:
    """只实现浏览器 viewer 端点用到的那几个成员。"""

    def __init__(self, *, protocols=(), cookies=None, token_query=None, origin=None):
        headers = {"host": "testserver"}
        if protocols:
            headers["sec-websocket-protocol"] = ", ".join(protocols)
        if origin:
            headers["origin"] = origin
        self.headers = headers
        self.cookies = dict(cookies or {})
        self.query_params = {"token": token_query} if token_query else {}
        self.accepted: str | None = None
        self.closed: int | None = None
        self.sent: list[dict] = []
        self.accept = AsyncMock(side_effect=self._accept)
        self.close = AsyncMock(side_effect=self._close)
        self.send_json = AsyncMock(side_effect=self._send_json)
        self.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

    async def _accept(self, subprotocol=None):
        self.accepted = subprotocol

    async def _close(self, code=1000):
        self.closed = code

    async def _send_json(self, payload, *args, **kwargs):
        self.sent.append(payload)


def _fake_db() -> SimpleNamespace:
    return SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock(), execute=AsyncMock())


class _SessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc_info):
        return False


def _browser_session(user_id: str = _OWNER_USER_ID) -> SimpleNamespace:
    return SimpleNamespace(
        id="session-1",
        user_id=user_id,
        approval_mode="autopilot",
        current_url="https://example.com",
        page_title="示例",
    )


def _patch_endpoint(db: SimpleNamespace | None = None, *, session=None, user_info=None):
    """把端点依赖整体替换掉：DB 会话、viewer token 解析、凭据校验。"""
    session = session or _browser_session()
    return (
        patch("app.api.v1.endpoints.browser.AsyncSessionLocal", lambda: _SessionContext(db or _fake_db())),
        patch(
            "app.api.v1.endpoints.browser.BrowserSessionService.resolve_viewer_token",
            AsyncMock(return_value=session),
        ),
        patch(
            "app.api.v1.endpoints.browser.AuthService.verify_api_key",
            AsyncMock(return_value=user_info),
        ),
    )


# --------------------------------------------------------------------------- 身份来源


def test_viewer_credential_is_read_from_the_auth_subprotocol_only():
    ws = _FakeWebSocket(
        protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_abc123"],
    )

    assert _viewer_credential_from_websocket(ws) == "sess_abc123"
    # 只有 viewer token（或 ?token=）时，没有「用户身份」可用：令牌不能自己证明归属。
    assert _viewer_credential_from_websocket(_FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}"])) is None
    assert _viewer_credential_from_websocket(_FakeWebSocket(token_query=_VIEWER_TOKEN)) is None


async def test_viewer_identity_prefers_the_explicit_credential_over_cookies():
    ws = _FakeWebSocket(
        protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_from_protocol"],
        cookies={"portal_session": "sess_from_cookie"},
    )
    verify = AsyncMock(return_value={"user_id": _OWNER_USER_ID})

    with patch("app.api.v1.endpoints.browser.AuthService.verify_api_key", verify):
        assert await _resolve_viewer_user_id(ws, _fake_db()) == _OWNER_USER_ID

    assert verify.await_count == 1
    assert verify.await_args.args[0] == "sess_from_protocol"


async def test_viewer_identity_falls_back_to_portal_then_embed_cookie():
    verify = AsyncMock(return_value={"user_id": _OWNER_USER_ID})

    with patch("app.api.v1.endpoints.browser.AuthService.verify_api_key", verify):
        portal_ws = _FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}"], cookies={"portal_session": "sess_portal"})
        assert await _resolve_viewer_user_id(portal_ws, _fake_db()) == _OWNER_USER_ID
        assert verify.await_args.args[0] == "sess_portal"

        verify.reset_mock()
        embed_ws = _FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}"], cookies={"embed_session": "sess_embed"})
        assert await _resolve_viewer_user_id(embed_ws, _fake_db()) == _OWNER_USER_ID
        assert verify.await_args.args[0] == "sess_embed"


async def test_viewer_identity_is_none_when_no_credential_is_available():
    verify = AsyncMock(return_value={"user_id": _OWNER_USER_ID})
    ws = _FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}"], token_query=_VIEWER_TOKEN)

    with patch("app.api.v1.endpoints.browser.AuthService.verify_api_key", verify):
        assert await _resolve_viewer_user_id(ws, _fake_db()) is None

    verify.assert_not_awaited()


async def test_viewer_identity_is_none_for_an_invalid_credential():
    ws = _FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_revoked"])

    with patch("app.api.v1.endpoints.browser.AuthService.verify_api_key", AsyncMock(return_value=None)):
        assert await _resolve_viewer_user_id(ws, _fake_db()) is None


async def test_viewer_identity_fails_closed_when_credential_verification_raises():
    ws = _FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_boom"])

    with patch(
        "app.api.v1.endpoints.browser.AuthService.verify_api_key",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        assert await _resolve_viewer_user_id(ws, _fake_db()) is None


# --------------------------------------------------------------------------- 端点行为


async def test_viewer_websocket_rejects_a_connection_without_user_identity():
    ws = _FakeWebSocket(protocols=[f"browser-viewer.{_VIEWER_TOKEN}"])
    open_session = AsyncMock()
    ctx, resolve, verify = _patch_endpoint()

    with ctx, resolve, verify, patch(
        "app.api.v1.endpoints.browser.browser_runtime.open_session", open_session
    ):
        await browser_viewer(ws, "session-1")

    assert ws.closed == 4403
    ws.accept.assert_not_awaited()
    open_session.assert_not_awaited()


async def test_viewer_websocket_rejects_another_users_identity():
    """拿到别人的 viewer token 也连不上：令牌不是身份。"""
    ws = _FakeWebSocket(
        protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_other_user"],
        cookies={"portal_session": "sess_other_user"},
    )
    open_session = AsyncMock()
    ctx, resolve, verify = _patch_endpoint(user_info={"user_id": _OTHER_USER_ID, "role": "user"})

    with ctx, resolve, verify, patch(
        "app.api.v1.endpoints.browser.browser_runtime.open_session", open_session
    ):
        await browser_viewer(ws, "session-1")

    assert ws.closed == 4403
    ws.accept.assert_not_awaited()
    open_session.assert_not_awaited()


async def test_viewer_websocket_rejects_an_admin_who_is_not_the_owner():
    """admin 也不能接管他人的浏览器会话（与聊天面的口径一致）。"""
    ws = _FakeWebSocket(
        protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_admin"],
    )
    open_session = AsyncMock()
    ctx, resolve, verify = _patch_endpoint(user_info={"user_id": _OTHER_USER_ID, "role": "admin"})

    with ctx, resolve, verify, patch(
        "app.api.v1.endpoints.browser.browser_runtime.open_session", open_session
    ):
        await browser_viewer(ws, "session-1")

    assert ws.closed == 4403
    ws.accept.assert_not_awaited()
    open_session.assert_not_awaited()


async def test_viewer_websocket_accepts_the_owner_and_streams_the_first_snapshot():
    ws = _FakeWebSocket(
        protocols=[f"browser-viewer.{_VIEWER_TOKEN}", "browser-auth.sess_owner"],
    )
    open_session = AsyncMock()
    ctx, resolve, verify = _patch_endpoint(user_info={"user_id": _OWNER_USER_ID, "role": "user"})

    with ctx, resolve, verify, patch(
        "app.api.v1.endpoints.browser.browser_runtime.open_session", open_session
    ), patch(
        "app.api.v1.endpoints.browser.browser_runtime.subscribe_events",
        Mock(return_value=asyncio.Queue()),
    ), patch(
        "app.api.v1.endpoints.browser.browser_runtime.unsubscribe_events", Mock()
    ), patch(
        "app.api.v1.endpoints.browser.browser_runtime.release_human_control", AsyncMock()
    ), patch(
        "app.api.v1.endpoints.browser.browser_runtime.has_session", Mock(return_value=False)
    ), patch(
        "app.api.v1.endpoints.browser.browser_runtime.get_ai_action", Mock(return_value=None)
    ), patch(
        "app.api.v1.endpoints.browser._viewer_snapshot", AsyncMock(return_value=SimpleNamespace())
    ), patch(
        "app.api.v1.endpoints.browser._viewer_snapshot_payload", Mock(return_value={"snapshot_id": "s1"})
    ), patch(
        "app.api.v1.endpoints.browser._send_viewer_control_state", AsyncMock()
    ), patch(
        "app.api.v1.endpoints.browser._send_viewer_tabs", AsyncMock()
    ):
        await browser_viewer(ws, "session-1")

    assert ws.closed is None, "归属人不应被拒绝"
    assert ws.accepted == f"browser-viewer.{_VIEWER_TOKEN}"
    assert ws.sent and ws.sent[0]["type"] == "snapshot"
    open_session.assert_awaited()
