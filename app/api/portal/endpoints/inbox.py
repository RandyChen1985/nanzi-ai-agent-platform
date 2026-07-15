from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_api_key
from app.core.orm import get_db_session
from app.services.portal_notification_service import PortalNotificationService

router = APIRouter()


def _item(row) -> Dict[str, Any]:
    return {
        "id": int(row.id), "category": row.category, "level": row.level,
        "title": row.title, "content": row.content, "resource_type": row.resource_type,
        "resource_id": row.resource_id, "metadata": row.meta_info or {},
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
async def list_inbox(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                     unread_only: bool = False, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    rows = await PortalNotificationService.list_for_user(db, int(user_info["user_id"]), limit=limit, offset=offset, unread_only=unread_only)
    return {"data": [_item(row) for row in rows]}


@router.get("/unread-count")
async def unread_count(user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    return {"data": {"count": await PortalNotificationService.unread_count(db, int(user_info["user_id"]))}}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    if not await PortalNotificationService.mark_read(db, int(user_info["user_id"]), notification_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    return {"status": "success"}


@router.post("/read-all")
async def mark_all_read(user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    return {"data": {"updated": await PortalNotificationService.mark_all_read(db, int(user_info["user_id"]))}}


@router.delete("/read")
async def delete_read_notifications(user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    return {"data": {"deleted": await PortalNotificationService.delete_read(db, int(user_info["user_id"]))}}


@router.delete("/{notification_id}")
async def delete_notification(notification_id: int, user_info=Depends(require_api_key), db: AsyncSession = Depends(get_db_session)):
    if not await PortalNotificationService.delete_one(db, int(user_info["user_id"]), notification_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    return {"status": "success"}
