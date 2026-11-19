"""客户端提前断开时，静态资源请求不应刷出 500 堆栈。

为什么值得测：``/assets/*.css``、``/assets/*.js`` 这类资源被浏览器取消是高频常态
（页面刷新、前端路由切换丢弃动态 import、代理超时）。Starlette 的
``BaseHTTPMiddleware.call_next()`` 会把它变成 ``RuntimeError("No response returned.")``
冒泡到全局异常处理器，于是用户毫无感知的一次资源取消就刷出两条 ERROR traceback，
把真正需要关注的 5xx 埋掉。

这里同时固定两个边界：
1. **只吞这一种错误**：下游真抛的异常必须继续冒泡，不能被兜底掩盖。
2. **只作用于非 /api 路径**：API 面的异常仍走原有链路。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.core.middleware import AccessLogMiddleware, _is_no_response_error


pytestmark = pytest.mark.no_infrastructure


ASSET_PATH = "/assets/useWorkspaceCanvas-BKEpjzd4.css"


def _build_scope(path: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 45678),
        "server": ("testserver", 80),
    }


async def _run_middleware(path: str, app) -> list:
    middleware = AccessLogMiddleware(app)
    sent: list = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await middleware(_build_scope(path), receive, send)
    return sent


def test_matches_only_the_no_response_error():
    assert _is_no_response_error(RuntimeError("No response returned."))
    assert _is_no_response_error(RuntimeError('prefix No response returned. suffix'))

    # 其他 RuntimeError / 异常不能被误判，否则真 bug 会被静默吞掉。
    assert not _is_no_response_error(RuntimeError("database is locked"))
    assert not _is_no_response_error(ValueError("No response returned."))
    assert not _is_no_response_error(asyncio.TimeoutError())


async def _silent_app(scope, receive, send) -> None:
    """模拟「客户端断开后下游被取消」：一个响应消息都不发就结束。"""
    return


async def test_asset_request_without_response_closes_as_499(caplog):
    with caplog.at_level(logging.WARNING, logger="app.core.middleware"):
        sent = await _run_middleware(ASSET_PATH, _silent_app)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 499

    # 只留一条不带堆栈的 warning，供排查用。
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert ASSET_PATH in warnings[0].getMessage()
    assert warnings[0].exc_info is None


async def test_downstream_exception_is_not_swallowed(caplog):
    async def boom_app(scope, receive, send):
        raise ValueError("静态资源真的坏了")

    with caplog.at_level(logging.WARNING, logger="app.core.middleware"):
        with pytest.raises(ValueError, match="静态资源真的坏了"):
            await _run_middleware(ASSET_PATH, boom_app)

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


async def test_normal_asset_response_passes_through():
    async def static_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/css")],
            }
        )
        await send({"type": "http.response.body", "body": b"body{}"})

    sent = await _run_middleware(ASSET_PATH, static_app)

    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b"body{}"


async def test_api_path_keeps_original_behaviour():
    """/api 命中断连时仍然冒泡：兜底只降噪静态资源，不改 API 面的错误语义。"""

    async def silent_api(scope, receive, send):
        return

    with pytest.raises(RuntimeError, match="No response returned."):
        await _run_middleware("/api/portal/agents", silent_api)
