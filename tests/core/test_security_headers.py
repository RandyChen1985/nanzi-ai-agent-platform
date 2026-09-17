"""安全响应头中间件的契约：只注入 header、不碰响应体，且不破坏嵌入能力。"""

import pytest
from starlette.types import Message

from app.core.middleware import SecurityHeadersMiddleware


pytestmark = pytest.mark.no_infrastructure


async def _dispatch(
    path: str,
    *,
    scheme: str = "http",
    content_type: str = "text/html",
    request_headers=None,
    body: bytes = b"ok",
):
    sent: list[Message] = []

    async def app(scope, receive, send):
        headers = [(b"content-type", content_type.encode())]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def receive():
        return {"type": "http.request"}

    async def send(message: Message):
        sent.append(message)

    scope = {
        "type": "http",
        "path": path,
        "scheme": scheme,
        "headers": list(request_headers or []),
    }
    await SecurityHeadersMiddleware(app)(scope, receive, send)

    header_map = {k.decode().lower(): v.decode() for k, v in sent[0]["headers"]}
    return header_map, sent


@pytest.mark.asyncio
async def test_html_response_gets_baseline_security_headers():
    headers, _ = await _dispatch("/dashboard")

    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    csp = headers["content-security-policy"]
    assert "frame-ancestors 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp


@pytest.mark.asyncio
async def test_embed_route_stays_embeddable_by_third_party():
    """嵌入对话必须仍能被第三方页面 iframe，否则组件交付能力直接失效。"""
    headers, _ = await _dispatch("/embed/chat")

    assert "x-frame-options" not in headers
    assert "frame-ancestors *" in headers["content-security-policy"]


@pytest.mark.asyncio
async def test_json_response_is_not_frame_restricted():
    """API/SSE 响应不是文档，加 frame 限制没有意义。"""
    headers, _ = await _dispatch("/api/portal/auth/me", content_type="application/json")

    assert headers["x-content-type-options"] == "nosniff"
    assert "x-frame-options" not in headers


@pytest.mark.asyncio
async def test_hsts_only_on_https():
    http_headers, _ = await _dispatch("/dashboard", scheme="http")
    assert "strict-transport-security" not in http_headers

    https_headers, _ = await _dispatch("/dashboard", scheme="https")
    hsts = https_headers["strict-transport-security"]
    assert "max-age=2592000" in hsts
    # 刻意不含 includeSubDomains：该域下可能存在仅 HTTP 的子服务，
    # 一旦下发就无法在用户端绕过。30 天窗口也便于快速纠错。
    assert "includeSubDomains" not in hsts


@pytest.mark.asyncio
async def test_forwarded_proto_https_from_reverse_proxy_enables_hsts():
    headers, _ = await _dispatch(
        "/dashboard", request_headers=[(b"x-forwarded-proto", b"https")]
    )

    assert "strict-transport-security" in headers


@pytest.mark.asyncio
async def test_existing_response_headers_are_preserved():
    """已经设置过的同名头不能被覆盖（例如上层中间件显式指定过）。"""
    headers, _ = await _dispatch("/dashboard")
    assert headers["content-type"] == "text/html"


@pytest.mark.asyncio
async def test_response_body_is_passed_through_untouched():
    """必须是纯 ASGI 头注入，不得缓冲响应体，否则会拖垮 SSE 流式输出。"""
    _, sent = await _dispatch(
        "/api/v1/chat/completions",
        content_type="text/event-stream",
        body=b"data: hi\n\n",
    )

    assert sent[1]["body"] == b"data: hi\n\n"
