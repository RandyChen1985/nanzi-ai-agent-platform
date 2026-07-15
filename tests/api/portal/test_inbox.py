from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import inbox
from app.services.portal_notification_service import PortalNotificationService


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_delete_notification_is_scoped_to_current_user():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(rowcount=1)

    deleted = await PortalNotificationService.delete_one(db, user_id=7, notification_id=31)

    assert deleted is True
    db.flush.assert_awaited_once()
    statement = str(db.execute.await_args.args[0])
    assert "portal_notifications.user_id" in statement
    assert "portal_notifications.id" in statement


@pytest.mark.asyncio
async def test_delete_notification_endpoint_returns_404_when_not_owned(monkeypatch):
    monkeypatch.setattr(PortalNotificationService, "delete_one", AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as exc_info:
        await inbox.delete_notification(31, user_info={"user_id": "7"}, db=AsyncMock())

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_clear_read_notifications_only_deletes_read_rows():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(rowcount=4)

    deleted = await PortalNotificationService.delete_read(db, user_id=7)

    assert deleted == 4
    statement = str(db.execute.await_args.args[0])
    assert "portal_notifications.user_id" in statement
    assert "portal_notifications.read_at IS NOT NULL" in statement

