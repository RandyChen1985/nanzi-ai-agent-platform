from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portal_notification import PortalNotification


class PortalNotificationService:
    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        user_id: int,
        title: str,
        content: str,
        level: str = "info",
        category: str = "saved_report",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PortalNotification:
        row = PortalNotification(
            user_id=user_id, title=title[:200], content=content, level=level,
            category=category, resource_type=resource_type, resource_id=resource_id,
            meta_info=metadata or {},
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def list_for_user(db: AsyncSession, user_id: int, *, limit: int, offset: int, unread_only: bool):
        stmt = select(PortalNotification).where(PortalNotification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(PortalNotification.read_at.is_(None))
        result = await db.execute(stmt.order_by(desc(PortalNotification.created_at)).offset(offset).limit(limit))
        return result.scalars().all()

    @staticmethod
    async def unread_count(db: AsyncSession, user_id: int) -> int:
        result = await db.execute(select(func.count()).select_from(PortalNotification).where(
            PortalNotification.user_id == user_id, PortalNotification.read_at.is_(None)
        ))
        return int(result.scalar() or 0)

    @staticmethod
    async def mark_read(db: AsyncSession, user_id: int, notification_id: int) -> bool:
        result = await db.execute(update(PortalNotification).where(
            PortalNotification.id == notification_id,
            PortalNotification.user_id == user_id,
            PortalNotification.read_at.is_(None),
        ).values(read_at=datetime.now()))
        await db.flush()
        return bool(result.rowcount)

    @staticmethod
    async def mark_all_read(db: AsyncSession, user_id: int) -> int:
        result = await db.execute(update(PortalNotification).where(
            PortalNotification.user_id == user_id,
            PortalNotification.read_at.is_(None),
        ).values(read_at=datetime.now()))
        await db.flush()
        return int(result.rowcount or 0)

    @staticmethod
    async def delete_one(db: AsyncSession, user_id: int, notification_id: int) -> bool:
        result = await db.execute(delete(PortalNotification).where(
            PortalNotification.id == notification_id,
            PortalNotification.user_id == user_id,
        ))
        await db.flush()
        return bool(result.rowcount)

    @staticmethod
    async def delete_read(db: AsyncSession, user_id: int) -> int:
        result = await db.execute(delete(PortalNotification).where(
            PortalNotification.user_id == user_id,
            PortalNotification.read_at.is_not(None),
        ))
        await db.flush()
        return int(result.rowcount or 0)
