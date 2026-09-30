"""全局 HTTPException 处理器必须保留 detail 与自定义响应头。

回归背景：该 handler 曾把 ``detail`` 写死为 ``None``（真实消息只放进 ``message``），
并且构造 ``JSONResponse`` 时丢弃了 ``exc.headers``。后果是前端读
``response.data.detail`` 永远拿到空串——包括浏览器环境的
``X-Browser-Error: environment_not_ready`` 标记也一并丢失，最终表现为只弹一句
默认 toast「打开服务端浏览器失败」，Playwright/Chromium 安装引导面板无法弹出。
"""

import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import http_exception_handler

pytestmark = pytest.mark.no_infrastructure


def _make_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/chat/browser/sessions/open",
            "headers": [],
            "query_string": b"",
        }
    )


@pytest.mark.asyncio
async def test_http_exception_handler_keeps_detail_and_custom_headers():
    exc = HTTPException(
        status_code=503,
        detail="服务端尚未下载 Chromium 浏览器内核。\n请在服务器终端执行：\nplaywright install chromium",
        headers={"X-Browser-Error": "environment_not_ready"},
    )

    response = await http_exception_handler(_make_request(), exc)
    payload = json.loads(response.body)

    assert payload["detail"], "HTTPException 的 detail 不得为空，否则前端只能显示默认文案"
    assert "Chromium" in payload["detail"]
    assert payload["message"] == payload["detail"], "detail 与 message 应保持一致，兼顾两套读法"
    assert (
        response.headers["x-browser-error"] == "environment_not_ready"
    ), "自定义响应头（如浏览器环境标记）必须透传，前端据此区分环境故障与普通失败"


@pytest.mark.asyncio
async def test_http_exception_handler_still_reports_structured_detail():
    """detail 为 dict 时，结构化内容要保留在 data 中，同时给出可读字符串。"""
    exc = HTTPException(status_code=400, detail={"code": "INVALID_URL", "message": "地址不被允许"})

    response = await http_exception_handler(_make_request(), exc)
    payload = json.loads(response.body)

    assert payload["detail"] == "地址不被允许"
    assert payload["data"] == {"code": "INVALID_URL", "message": "地址不被允许"}
