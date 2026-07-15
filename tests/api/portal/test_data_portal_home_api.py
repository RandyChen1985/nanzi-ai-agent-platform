from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_data_portal_home_endpoint_passes_current_user_and_roles(monkeypatch):
    from app.api.portal.endpoints import data_portal

    payload = {
        "attention": {},
        "recent_analysis": [],
        "report_summary": {"items": []},
        "generated_at": "2026-07-15T10:00:00",
    }
    build = AsyncMock(return_value=payload)
    monkeypatch.setattr(data_portal, "get_user_role_ids", AsyncMock(return_value=[2, 3]))
    monkeypatch.setattr(data_portal.DataPortalHomeService, "build", build)

    response = await data_portal.get_data_portal_home(
        user_info={"user_id": "7", "user_name": "alice"},
        db=AsyncMock(),
    )

    assert response.data == payload
    build.assert_awaited_once()
    assert build.await_args.kwargs["user_id"] == 7
    assert build.await_args.kwargs["role_ids"] == [2, 3]
