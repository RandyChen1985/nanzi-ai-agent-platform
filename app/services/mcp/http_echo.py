"""内置 HTTP 回显端点：验收「API 工具的凭据注入」到底发出去没有。

和 echo MCP 是同一类东西，区别只在传输层：echo MCP 验收 MCP 的 `tools/call`，
这里验收普通 API 工具的 `headers / query / body`。

**为什么内置而不是让用户去连一个外部 echo 服务**：外部服务会被网络、代理、证书
干扰，出问题时分不清是凭据没注入还是请求没发出去。内置端点只在进程内跑，
唯一的变量就是「注入了什么」。

**为什么只回显指纹和掩码**：验收要回答的是「发出去的是 A 那条还是 B 那条」，
指纹可以；回显明文等于把凭据写进响应体、日志和 trace。

只在非 production 环境挂载（见 ``app/main.py``），生产环境这个路由不存在。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

echo_router = APIRouter()

#: 这些头是传输层自己加的，回显出来只会误导排查
_IGNORED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "user-agent",
    "accept",
}


def _mask(value: str, *, keep: int = 4) -> str | None:
    """留头尾各几位，中间打码。太短的值直接全码，否则等于没码。"""
    text = str(value or "")
    if not text:
        return None
    if len(text) <= keep * 2:
        return "*" * len(text)
    return f"{text[:keep]}***{text[-keep:]}"


def _fingerprint(value: str) -> str | None:
    """SHA-256 前 12 位。同一条凭据的指纹稳定，用来断言「注入的是哪一条」。"""
    text = str(value or "")
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _mask_mapping(data: Any) -> Any:
    """把 JSON 里的标量值掩码；嵌套结构原样保留形状。"""
    if isinstance(data, dict):
        return {str(key): _mask_mapping(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_mask_mapping(item) for item in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return _mask(str(data)) if data is not None else None
    return "<非标量>"


def _fingerprint_mapping(data: Any) -> Any:
    if isinstance(data, dict):
        return {str(key): _fingerprint_mapping(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_fingerprint_mapping(item) for item in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return _fingerprint(str(data)) if data is not None else None
    return None


@echo_router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def http_echo(request: Request) -> dict[str, Any]:
    """回显收到的请求，值一律掩码 + 指纹。"""
    raw = await request.body()
    body: Any = None
    if raw:
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            body = {"_raw_preview": raw[:200].decode("utf-8", errors="replace")}

    headers = {
        name.lower(): value
        for name, value in request.headers.items()
        if name.lower() not in _IGNORED_HEADERS
    }

    return {
        "ok": True,
        "method": request.method,
        "path": request.url.path,
        # query 值可能是凭据（参数级注入走 query），所以也走掩码 + 指纹
        "query": {key: _mask(value) for key, value in request.query_params.items()},
        "query_fingerprint": {
            key: _fingerprint(value) for key, value in request.query_params.items()
        },
        # body 里同样可能有凭据（参数级注入走 query/body），所以也要掩码
        "body": _mask_mapping(body) if body is not None else None,
        "body_fingerprint": _fingerprint_mapping(body) if body is not None else None,
        "headers_masked": {name: _mask(value) for name, value in headers.items()},
        "headers_fingerprint": {name: _fingerprint(value) for name, value in headers.items()},
    }
