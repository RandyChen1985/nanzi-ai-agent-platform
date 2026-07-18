from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_workbench_home_passes_current_user_and_roles(monkeypatch):
    from app.api.portal.endpoints import workbench

    payload = {"mode": "new_user", "attention": []}
    build = AsyncMock(return_value=payload)
    monkeypatch.setattr(workbench, "get_user_role_ids", AsyncMock(return_value=[2, 3]))
    monkeypatch.setattr(workbench.WorkbenchHomeService, "build", build)

    response = await workbench.get_workbench_home(
        user_info={"user_id": "7", "user_name": "alice", "role": "user"},
        db=AsyncMock(),
    )

    assert response.data == payload
    build.assert_awaited_once()
    assert build.await_args.kwargs["user_id"] == 7
    assert build.await_args.kwargs["role_ids"] == [2, 3]
    assert build.await_args.kwargs["user"]["user_name"] == "alice"

