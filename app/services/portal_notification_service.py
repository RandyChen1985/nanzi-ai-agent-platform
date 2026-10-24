from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import delete, desc, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
        target_uid = int(user_id) if user_id is not None else 0
        row = PortalNotification(
            user_id=target_uid, title=title[:200], content=content, level=level,
            category=category, resource_type=resource_type, resource_id=resource_id,
            meta_info=metadata or {},
        )
        db.add(row)
        await db.flush()
        return row

    @staticmethod
    async def list_for_user(db: AsyncSession, user_id: int, *, limit: int, offset: int, unread_only: bool):
        target_uid = int(user_id) if user_id is not None else 0
        stmt = select(PortalNotification).where(PortalNotification.user_id == target_uid)
        if unread_only:
            stmt = stmt.where(PortalNotification.read_at.is_(None))
        result = await db.execute(stmt.order_by(desc(PortalNotification.created_at)).offset(offset).limit(limit))
        return result.scalars().all()

    @staticmethod
    async def unread_count(db: AsyncSession, user_id: int) -> int:
        target_uid = int(user_id) if user_id is not None else 0
        result = await db.execute(select(func.count()).select_from(PortalNotification).where(
            PortalNotification.user_id == target_uid, PortalNotification.read_at.is_(None)
        ))
        return int(result.scalar() or 0)

    @staticmethod
    async def mark_read(db: AsyncSession, user_id: int, notification_id: int) -> bool:
        target_uid = int(user_id) if user_id is not None else 0
        result = await db.execute(update(PortalNotification).where(
            PortalNotification.id == notification_id,
            PortalNotification.user_id == target_uid,
            PortalNotification.read_at.is_(None),
        ).values(read_at=datetime.now()))
        await db.flush()
        return bool(result.rowcount)

    @staticmethod
    async def mark_all_read(db: AsyncSession, user_id: int) -> int:
        target_uid = int(user_id) if user_id is not None else 0
        result = await db.execute(update(PortalNotification).where(
            PortalNotification.user_id == target_uid,
            PortalNotification.read_at.is_(None),
        ).values(read_at=datetime.now()))
        await db.flush()
        return int(result.rowcount or 0)

    @staticmethod
    async def delete_one(db: AsyncSession, user_id: int, notification_id: int) -> bool:
        target_uid = int(user_id) if user_id is not None else 0
        result = await db.execute(delete(PortalNotification).where(
            PortalNotification.id == notification_id,
            PortalNotification.user_id == target_uid,
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

    @staticmethod
    async def purge_older_than(
        db: AsyncSession,
        retention_days: int,
        *,
        batch_size: int = 5000,
    ) -> int:
        """删除早于保留期的站内信，返回删除行数。

        由每日 ``system_audit_log_maintenance`` 任务调用（保留天数在调度层写死）。
        未读消息同样按时间清理：超过保留期的通知已失去时效价值，长期堆积会让
        用户一登录就面对大量陈旧未读，反而掩盖真正需要处理的消息。

        采用「按批 DELETE + 逐批提交」，避免大表一次性删除造成长事务、锁表与
        undo 膨胀；MySQL 与 PostgreSQL 分方言处理（PG 不支持 DELETE ... LIMIT）。
        """
        if retention_days <= 0:
            return 0

        threshold = datetime.now() - timedelta(days=retention_days)
        table = PortalNotification.__tablename__
        if settings.normalized_database_type == "postgresql":
            sql = text(
                f'DELETE FROM "{table}" WHERE ctid IN ('
                f'SELECT ctid FROM "{table}" WHERE created_at < :threshold '
                f"LIMIT :batch_size)"
            )
        else:
            sql = text(
                f"DELETE FROM `{table}` WHERE created_at < :threshold LIMIT :batch_size"
            )

        deleted_total = 0
        while True:
            result = await db.execute(
                sql, {"threshold": threshold, "batch_size": batch_size}
            )
            await db.commit()
            deleted = int(result.rowcount or 0)
            deleted_total += deleted
            if deleted < batch_size:
                break
        return deleted_total
