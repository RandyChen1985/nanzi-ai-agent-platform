"""访客视角的只读分享接口（免鉴权）。

三条路径都挂在 ``public_router``（不经 ``require_api_key``）：

- ``GET /{share_id}``         元信息，不含任何消息内容，因此不计数；
- ``POST /{share_id}/unlock`` 校验访问密码，签发短期令牌；
- ``GET /{share_id}/content`` 返回冻结快照，成功即 +1 访问计数。

失效统一 404：不存在 / 已作废 / 已过期对访客是同一句话，避免被枚举探测。
响应头带 ``X-Robots-Tag``，防止分享页被搜索引擎收录。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services import conversation_share_service as share_service

logger = logging.getLogger(__name__)

public_router = APIRouter()

#: 访客页面一律不收录：分享链接是「有链接就能看」，被搜索引擎抓到等于公开
NOINDEX_HEADERS = {"X-Robots-Tag": "noindex, nofollow"}

#: 失效对外的统一措辞（不存在 / 已作废 / 已过期不再区分）
GONE_DETAIL = "链接不存在或已失效"


class UnlockPayload(BaseModel):
    """解锁请求体：只收访问密码。"""

    secret: str


def _client_ip(request: Request) -> str:
    """取客户端 IP：优先 ``x-forwarded-for`` 首段（反向代理后），回退直连地址。"""
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _optional_viewer_user_id(request: Request, db: AsyncSession) -> Optional[str]:
    """可选鉴权：能认出分享人本人就免密码，认不出就当匿名，绝不因凭据问题把页面打成 401。

    ``public_router`` 不跑 ``require_api_key``，所以这里自己尝试解析一次；
    任何异常都吞掉并降级为匿名——访客页面的第一优先级是「能看」，而不是「严格认证」。
    """
    raw = (
        request.headers.get("x-api-key")
        or (request.headers.get("authorization") or "").replace("Bearer ", "").strip()
        or request.cookies.get("portal_session")
        or request.cookies.get("embed_session")
    )
    if not raw:
        return None
    try:
        from app.services.auth_service import AuthService

        info = await AuthService.verify_api_key(raw, db)
    except Exception:  # noqa: BLE001 - 凭据无效/过期一律当匿名，不能影响访客阅读
        return None
    return str((info or {}).get("user_id") or "") or None


@public_router.get(
    "/{share_id}",
    response_model=StandardResponse[Dict[str, Any]],
    summary="分享元信息",
)
async def get_share_meta(
    share_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    """访客页首屏：标题、智能体名、访问方式、剩余有效期；**不含正文**。"""
    response.headers.update(NOINDEX_HEADERS)
    try:
        return StandardResponse(data=await share_service.publish_public_meta(db, share_id))
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc


@public_router.post(
    "/{share_id}/unlock",
    response_model=StandardResponse[Dict[str, Any]],
    summary="解锁分享",
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
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc
    except share_service.ShareRateLimitedError as exc:
        raise HTTPException(status_code=429, detail="尝试次数过多，请稍后再试", headers=NOINDEX_HEADERS) from exc
    except share_service.ShareAccessDeniedError as exc:
        raise HTTPException(status_code=401, detail="访问密码不正确", headers=NOINDEX_HEADERS) from exc
    except ValueError as exc:
        # public 分享本身不需要解锁：这是调用方的用法错误，不是访客的密码错误
        raise HTTPException(status_code=400, detail=str(exc), headers=NOINDEX_HEADERS) from exc
    return StandardResponse(data={"token": token, "expires_in": expires_in})


@public_router.get(
    "/{share_id}/content",
    response_model=StandardResponse[Dict[str, Any]],
    summary="分享内容",
)
async def get_share_content(
    share_id: str,
    request: Request,
    response: Response,
    token: Optional[str] = Query(default=None, description="解锁令牌；也可用请求头 X-Share-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """读冻结正文：唯一会给访问计数 +1 的入口；分享人本人带登录态可免密码。"""
    response.headers.update(NOINDEX_HEADERS)
    try:
        data = await share_service.read_share_content(
            db,
            share_id=share_id,
            token=token or request.headers.get("x-share-token"),
            viewer_user_id=await _optional_viewer_user_id(request, db),
            client_ip=_client_ip(request),
        )
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail=GONE_DETAIL, headers=NOINDEX_HEADERS) from exc
    except share_service.ShareAccessDeniedError as exc:
        raise HTTPException(status_code=401, detail="需要访问密码", headers=NOINDEX_HEADERS) from exc
    return StandardResponse(data=data)
