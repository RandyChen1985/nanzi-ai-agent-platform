"""工作区文件在线分享（所有者视角）。

只服务登录用户管理自己的分享：创建 / 列表 / 作废。访客下载走免鉴权的
``/api/v1/share/file/*``（见 ``app/api/v1/endpoints/file_share.py``）。

与会话分享（``chat_shares.py``）刻意保持同构：同样的有效期入参、同样的「查不到与不是你的
一律 404」、同样的对外链接拼装方式。差别只有一处——创建时收的是**文件路径**而不是会话 ID。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.schemas.response import StandardResponse
from app.services import conversation_share_service as conv_share
from app.services import file_share_service as share_service
from app.services.ai.conversation_identity import MissingUserIdentityError, require_user_id
from app.services.app_public_url_service import get_app_public_url

logger = logging.getLogger(__name__)

router = APIRouter()


class ExpiryPayload(BaseModel):
    """前端有效期选项：与会话分享同一份定义，直接复用 ``resolve_expires_at``。"""

    mode: Literal["preset", "custom", "permanent"] = "preset"
    preset: Optional[str] = "7d"
    value: Optional[int] = None
    unit: Optional[Literal["hour", "day"]] = None


class CreateFileSharePayload(BaseModel):
    """创建文件分享：路径按工作区语义传（绝对路径或相对工作区根均可）。

    ``artifact_id`` 是给生成类产物用的第二条入口：图片/导出件的产物落在
    ``<user_key>/generated/`` 下，但前端只拿得到 ``download_url``、没有工作区路径，
    于是把已经在工作区里的文件误判成「未落盘」。传 ID 让后端自己反查路径，
    既不必暴露服务器路径，也不用前端拼路径猜。
    """

    path: Optional[str] = Field(default=None, max_length=1024)
    artifact_id: Optional[str] = Field(default=None, max_length=64)
    access_mode: Literal["public", "secret"] = "secret"
    secret: Optional[str] = None
    expiry: ExpiryPayload = Field(default_factory=ExpiryPayload)

    @model_validator(mode="after")
    def _require_one_source(self):
        """``path`` 与 ``artifact_id`` 至少给一个，否则后端只能回一句「路径为空」。"""
        if not (self.path or self.artifact_id):
            raise ValueError("path 与 artifact_id 至少提供一个")
        return self


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
    return f"{base}/s/{share_id}"


def _plain_secret(row: Any) -> Optional[str]:
    """回显明文密码：列表里要能反复「复制密码」；解不开（密钥轮换）返回 None 而不是 500。"""
    if not row.secret_cipher:
        return None
    return conv_share.decrypt_secret(row.secret_cipher)


def _serialize(row: Any, secret: Optional[str]) -> Dict[str, Any]:
    """行 → 响应字段；不含任何磁盘路径，前端拿到的就是可展示的那几项。"""
    return {
        "share_id": row.share_id,
        "file_name": row.file_name,
        "file_ext": row.file_ext or "",
        "file_size": int(row.file_size or 0),
        "mime_type": row.mime_type or "",
        "access_mode": row.access_mode,
        "secret": secret,
        "status": conv_share.derive_share_status(row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "download_count": int(row.download_count or 0),
        "last_downloaded_at": (
            row.last_downloaded_at.isoformat() if row.last_downloaded_at else None
        ),
    }


@router.post("", response_model=StandardResponse[Dict[str, Any]], summary="创建文件分享")
async def create_share(
    payload: CreateFileSharePayload,
    request: Request,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    _current_user(user_info)  # 身份不可靠时直接 401
    try:
        row = await share_service.create_share(
            db,
            user_info=user_info,
            raw_path=payload.path,
            artifact_id=payload.artifact_id,
            access_mode=payload.access_mode,
            secret=payload.secret,
            expiry=payload.expiry.model_dump(),
        )
    except share_service.FileShareTargetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        # resolve_expires_at 的自定义区间校验走这里
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    data = _serialize(row, _plain_secret(row))
    # 明文只在创建响应里出现一次：前端「复制分享信息」用它，之后只回显解密结果
    data["secret"] = row.__dict__.get("plaintext_secret") or data.get("secret")
    data["url"] = await _build_share_url(row.share_id, request)
    return StandardResponse(data=data)


@router.get("", response_model=StandardResponse[Dict[str, Any]], summary="我分享的文件链接")
async def list_shares(
    request: Request,
    include_revoked: bool = False,
    user_info: Dict[str, Any] = Depends(require_api_key),
    db: AsyncSession = Depends(get_db_session),
):
    user_id, _username = _current_user(user_info)
    rows = await share_service.list_shares(
        db, user_id=user_id, include_revoked=include_revoked
    )
    items: List[Dict[str, Any]] = []
    for row in rows:
        item = _serialize(row, _plain_secret(row))
        item["url"] = await _build_share_url(row.share_id, request)
        items.append(item)
    return StandardResponse(data={"total": len(items), "items": items})


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
