"""会话在线分享（所有者视角）。

只服务登录用户管理自己的分享：创建 / 列表 / 作废。访客读取走免鉴权的
``/api/v1/share/*``。

「查不到」与「不是你的」一律 404：分开返回会让攻击者拿会话 ID 探测别人的数据。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services import conversation_share_service as share_service
from app.services.ai.conversation_identity import MissingUserIdentityError, require_user_id
from app.services.app_public_url_service import get_app_public_url

logger = logging.getLogger(__name__)

router = APIRouter()


class ExpiryPayload(BaseModel):
    """前端有效期选项：预设 / 自定义数量 + 单位 / 永久。"""

    mode: Literal["preset", "custom", "permanent"] = "preset"
    preset: Optional[str] = "7d"
    value: Optional[int] = None
    unit: Optional[Literal["hour", "day"]] = None


class CreateSharePayload(BaseModel):
    """创建分享的请求体；密码由前端生成后传入（合法长度由服务层校验）。"""

    conversation_id: str = Field(min_length=1, max_length=50)
    access_mode: Literal["public", "secret"] = "secret"
    secret: Optional[str] = None
    expiry: ExpiryPayload = Field(default_factory=ExpiryPayload)


def _current_user(user_info: Dict[str, Any]) -> tuple[str, str]:
    """取当前用户 ID 与用户名；身份不可靠时 401，不做匿名降级。"""
    try:
        user_id = require_user_id(user_info)
    except MissingUserIdentityError as exc:
        raise HTTPException(status_code=401, detail="当前用户身份无效") from exc
    username = str(user_info.get("username") or user_info.get("user_name") or "")
    return user_id, username


async def _build_share_url(share_id: str, request: Request) -> str:
    """拼对外链接：优先系统配置的公开地址，未配置时回退到本次请求的 base_url。"""
    base = await get_app_public_url()
    if not base:
        base = str(request.base_url).rstrip("/")
    return f"{base}/share/{share_id}"


def _serialize(row, secret: Optional[str]) -> Dict[str, Any]:
    """行 → 响应字段；``secret`` 是解密后的明文（解不开则为 None，前端据此禁用复制）。"""
    return {
        "share_id": row.share_id,
        "title": row.title or "",
        "agent_name": row.agent_name or "",
        "access_mode": row.access_mode,
        "secret": secret,
        "status": share_service.derive_share_status(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "view_count": int(row.view_count or 0),
        "last_viewed_at": row.last_viewed_at.isoformat() if row.last_viewed_at else None,
        "turn_count": int(row.turn_count or 0),
        "message_count": int(row.message_count or 0),
    }


def _plain_secret(row) -> Optional[str]:
    """回显明文密码：需求要求列表里能反复「复制密码」。

    解密失败（密钥轮换后老分享）返回 None，而不是让整个列表 500。
    """
    if not row.secret_cipher:
        return None
    return share_service.decrypt_secret(row.secret_cipher)


@router.post("", response_model=StandardResponse[Dict[str, Any]], summary="创建会话分享")
async def create_share(
    payload: CreateSharePayload,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, username = _current_user(user_info)
    try:
        expires_at = share_service.resolve_expires_at(payload.expiry.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        row = await share_service.create_share(
            db,
            owner_user_id=user_id,
            owner_username=username,
            conversation_id=payload.conversation_id,
            access_mode=payload.access_mode,
            secret=payload.secret,
            expires_at=expires_at,
        )
    except share_service.ShareNotFoundError as exc:
        raise HTTPException(status_code=404, detail="会话不存在或不属于当前用户") from exc
    except share_service.ShareContentTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = _serialize(row, _plain_secret(row))
    data["url"] = await _build_share_url(row.share_id, request)
    return StandardResponse(data=data)


@router.get("", response_model=StandardResponse[Dict[str, Any]], summary="我分享的会话链接")
async def list_shares(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: Optional[str] = None,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _username = _current_user(user_info)
    rows, total = await share_service.list_shares(
        db, user_id=user_id, page=page, page_size=page_size, status=status
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        item = _serialize(row, _plain_secret(row))
        item["url"] = await _build_share_url(row.share_id, request)
        items.append(item)
    return StandardResponse(
        data={"total": total, "page": page, "page_size": page_size, "items": items}
    )


@router.post("/{share_id}/revoke", response_model=StandardResponse[Dict[str, Any]], summary="作废分享")
async def revoke_share(
    share_id: str,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _username = _current_user(user_info)
    ok = await share_service.revoke_share(db, user_id=user_id, share_id=share_id)
    if not ok:
        # 非本人与不存在同样 404：不能让人拿 share_id 试探归属
        raise HTTPException(status_code=404, detail="分享不存在")
    return StandardResponse(data={"revoked": True})
