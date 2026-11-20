"""访客视角的文件分享接口（免鉴权）。

三条路径都挂在 ``public_router``（不经 ``require_api_key``）：

- ``GET /{share_id}``          元信息（文件名、大小、有效期），不含磁盘路径，因此不计数；
- ``POST /{share_id}/unlock``  校验访问密码，签发短期令牌；
- ``GET /{share_id}/download`` 校验通过后返回 ``FileResponse``，成功即 +1 下载计数。

与会话分享（``share.py``）同一套对外语义：失效统一 404、响应头带 ``X-Robots-Tag``
防收录、能从登录态认出分享人本人时免密码。

**安全边界**：下载路径只来自分享记录，绝不接受请求里的路径参数。记录里的路径还要再经
``resolve_share_abs_path`` 复核一次是否仍在分享人目录内（防目录被换成符号链接），并对内容
做 sha256 比对——文件一旦被改写就拒绝，这正是「不复制文件也能冻结内容」的实现方式。
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services import file_share_service as share_service
from app.api.v1.endpoints.share import (
    GONE_DETAIL,
    NOINDEX_HEADERS,
    _client_ip,
    _optional_viewer_user_id,
)

logger = logging.getLogger(__name__)

public_router = APIRouter()


class UnlockPayload(BaseModel):
    """解锁请求体：只收访问密码。"""

    secret: str


@public_router.get(
    "/{share_id}",
    response_model=StandardResponse[Dict[str, Any]],
    summary="文件分享元信息",
)
async def get_share_meta(
    share_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    """访客页首屏：文件名、大小、剩余有效期；**不含磁盘路径**，也不计数。"""
    response.headers.update(NOINDEX_HEADERS)
    try:
        return StandardResponse(data=await share_service.publish_public_meta(db, share_id))
    except share_service.FileShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc


@public_router.post(
    "/{share_id}/unlock",
    response_model=StandardResponse[Dict[str, Any]],
    summary="解锁文件分享",
)
async def unlock_share(
    share_id: str,
    payload: UnlockPayload,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    """校验访问密码并签发短期令牌；失败次数由 Redis 限流兜住。"""
    response.headers.update(NOINDEX_HEADERS)
    try:
        token, expires_in = await share_service.unlock_share(
            db, share_id=share_id, secret=payload.secret, client_ip=_client_ip(request)
        )
    except share_service.FileShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc
    except share_service.FileShareRateLimitedError as exc:
        raise HTTPException(
            status_code=429, detail="尝试次数过多，请稍后再试", headers=NOINDEX_HEADERS
        ) from exc
    except share_service.FileShareAccessDeniedError as exc:
        raise HTTPException(
            status_code=401, detail="访问密码不正确", headers=NOINDEX_HEADERS
        ) from exc
    except ValueError as exc:
        # public 分享本身不需要解锁：这是调用方的用法错误，不是访客的密码错误
        raise HTTPException(status_code=400, detail=str(exc), headers=NOINDEX_HEADERS) from exc
    return StandardResponse(data={"token": token, "expires_in": expires_in})


def _content_disposition(file_name: str) -> str:
    """中文文件名必须走 RFC 5987：``filename*`` 带 UTF-8 百分号编码。

    同时保留 ASCII 兜底 ``filename``，极老的客户端至少能拿到一个不含乱码的近似名。
    """
    fallback = "".join(ch if ord(ch) < 128 and ch not in '"\\' else "_" for ch in file_name) or "file"
    encoded = urllib.parse.quote(file_name, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _inline_disposition(file_name: str) -> str:
    """在线预览用 ``inline``：浏览器直接渲染，不弹下载框。"""
    fallback = "".join(ch if ord(ch) < 128 and ch not in '"\\' else "_" for ch in file_name) or "file"
    encoded = urllib.parse.quote(file_name, safe="")
    return f'inline; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


def _preview_headers(meta: Dict[str, Any]) -> Dict[str, str]:
    """按 preview_kind 给响应头。

    **html/svg 必须带 CSP sandbox**：这是「允许页面内的脚本运行、但拿不到平台身份」的关键。
    只靠外层 ``<iframe sandbox>`` 是不够的——访客把 iframe 地址复制到地址栏直接打开，
    属性就绕过了，而响应头依然生效。
    """
    kind = meta.get("preview_kind") or "download"
    headers: Dict[str, str] = {
        **NOINDEX_HEADERS,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        # 内容随文件更新而失效，禁止中间层缓存旧内容
        "Cache-Control": "no-store",
        "Content-Disposition": _inline_disposition(meta.get("file_name") or "file"),
    }
    if kind == "html":
        headers["Content-Security-Policy"] = share_service.HTML_SANDBOX_CSP
    elif kind == "svg":
        headers["Content-Security-Policy"] = share_service.SVG_SANDBOX_CSP
    return headers


@public_router.get(
    "/{share_id}/raw",
    summary="在线预览：以 inline 方式返回原始字节",
    response_class=FileResponse,
    response_model=None,
)
async def preview_shared_file(
    share_id: str,
    request: Request,
    response: Response,
    token: Optional[str] = Query(default=None, description="解锁令牌；也可用请求头 X-Share-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """HTML / 图片 / PDF / SVG 的页内渲染来源。

    不可预览的格式在这里也会返回字节（浏览器会因 Content-Type 变成下载），但前端不会对
    这类文件调用本端点——``preview_kind == "download"`` 时直接用 ``/download``。
    """
    try:
        abs_path, meta = await share_service.resolve_inline(
            db,
            share_id=share_id,
            token=token or request.headers.get("x-share-token"),
            viewer_user_id=await _optional_viewer_user_id(request, db),
            # HEAD 探测（落地页判断令牌是否还有效）不算一次预览
            count=request.method != "HEAD",
        )
    except share_service.FileShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc
    except share_service.FileShareAccessDeniedError as exc:
        raise HTTPException(status_code=401, detail="需要访问密码", headers=NOINDEX_HEADERS) from exc
    except share_service.FileShareTargetError as exc:
        raise HTTPException(status_code=410, detail=str(exc), headers=NOINDEX_HEADERS) from exc

    return FileResponse(
        abs_path,
        media_type=meta.get("mime_type") or "application/octet-stream",
        headers=_preview_headers(meta),
    )


@public_router.get(
    "/{share_id}/text",
    summary="在线预览：文本内容（供页内渲染 markdown / csv / 代码）",
)
async def read_shared_text(
    share_id: str,
    request: Request,
    token: Optional[str] = Query(default=None, description="解锁令牌；也可用请求头 X-Share-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """文本类内容走 JSON 而不是裸字节。

    这样前端能拿到 ``truncated`` 标记，也能避免浏览器把 ``.md`` 当纯文本下载；
    超限只读前 2 MB，超出的部分明确告诉访客「请下载完整文件」。
    """
    try:
        text, truncated, meta = await share_service.read_share_text(
            db,
            share_id=share_id,
            token=token or request.headers.get("x-share-token"),
            viewer_user_id=await _optional_viewer_user_id(request, db),
        )
    except share_service.FileShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc
    except share_service.FileShareAccessDeniedError as exc:
        raise HTTPException(status_code=401, detail="需要访问密码", headers=NOINDEX_HEADERS) from exc
    except share_service.FileShareTargetError as exc:
        raise HTTPException(status_code=410, detail=str(exc), headers=NOINDEX_HEADERS) from exc

    return StandardResponse(
        data={
            "content": text,
            "truncated": truncated,
            "max_bytes": share_service.MAX_PREVIEW_TEXT_BYTES,
            "file": meta,
        }
    )


@public_router.get(
    "/{share_id}/download",
    summary="下载分享的文件",
    response_class=FileResponse,
)
async def download_shared_file(
    share_id: str,
    request: Request,
    response: Response,
    token: Optional[str] = Query(default=None, description="解锁令牌；也可用请求头 X-Share-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """读文件流：唯一会给下载计数 +1 的入口；分享人本人带登录态可免密码。"""
    response.headers.update(NOINDEX_HEADERS)
    try:
        abs_path, meta = await share_service.resolve_download(
            db,
            share_id=share_id,
            token=token or request.headers.get("x-share-token"),
            viewer_user_id=await _optional_viewer_user_id(request, db),
            client_ip=_client_ip(request),
            # 落地页用 HEAD 探测「令牌还有效吗 / 文件还是那一版吗」，探测不算一次下载
            count=request.method != "HEAD",
        )
    except share_service.FileShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc
    except share_service.FileShareAccessDeniedError as exc:
        raise HTTPException(
            status_code=401, detail="需要访问密码", headers=NOINDEX_HEADERS
        ) from exc
    except share_service.FileShareTargetError as exc:
        # 文件被删/被改写：链接本身还有效，但这版内容已经不在了
        raise HTTPException(
            status_code=410, detail=str(exc), headers=NOINDEX_HEADERS
        ) from exc

    return FileResponse(
        abs_path,
        media_type=meta.get("mime_type") or "application/octet-stream",
        headers={
            **NOINDEX_HEADERS,
            "Content-Disposition": _content_disposition(meta.get("file_name") or "file"),
            # 分享内容会随文件更新而失效，禁止中间层缓存旧内容
            "Cache-Control": "no-store",
        },
    )
