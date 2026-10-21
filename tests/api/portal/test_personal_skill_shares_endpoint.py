"""个人技能分享接口契约：路由顺序、参数校验与领域异常到 HTTP 的映射。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints import personal_skills
from app.services import skill_share_service


pytestmark = pytest.mark.no_infrastructure

USER = {"user_id": 7, "user_name": "alice"}
SESSION = SimpleNamespace()


def _route_paths():
    return [route.path for route in personal_skills.router.routes]


def test_share_targets_route_is_registered_before_skill_id_route():
    paths = _route_paths()
    assert "/share-targets" in paths
    assert paths.index("/share-targets") < paths.index("/{skill_id}")


def test_share_routes_are_registered():
    paths = _route_paths()
    assert "/shares/summary" in paths
    assert "/shares/sent" in paths
    assert "/shares/sent/{share_id}" in paths
    assert "/shares/received" in paths
    assert "/shares/received/{share_id}/accept" in paths
    assert "/shares/received/{share_id}/reject" in paths
    assert "/{skill_id}/shares" in paths


@pytest.mark.asyncio
async def test_create_share_returns_service_payload():
    payload = {"created": [{"id": "s1"}], "refreshed": []}
    with patch.object(personal_skills, "share_skill", new=AsyncMock(return_value=payload)) as share:
        result = await personal_skills.create_personal_skill_share(
            skill_id="summarizer",
            payload={"target_user_ids": [9], "message": "hi"},
            user=USER,
            session=SESSION,
        )
    assert result["status"] == "success"
    assert result["data"] == payload
    assert share.await_args.kwargs["skill_id"] == "summarizer"
    assert share.await_args.kwargs["target_user_ids"] == [9]


@pytest.mark.asyncio
async def test_create_share_maps_value_error_to_400():
    with patch.object(
        personal_skills,
        "share_skill",
        new=AsyncMock(side_effect=ValueError("不能分享给自己")),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await personal_skills.create_personal_skill_share(
                skill_id="summarizer",
                payload={"target_user_ids": [7]},
                user=USER,
                session=SESSION,
            )
    assert excinfo.value.status_code == 400
    assert "不能分享给自己" in excinfo.value.detail


@pytest.mark.asyncio
async def test_accept_share_maps_conflict_and_permission():
    with patch.object(
        personal_skills,
        "accept_share",
        new=AsyncMock(side_effect=skill_share_service.ShareConflictError("该分享已处理")),
    ):
        with pytest.raises(HTTPException) as conflict:
            await personal_skills.accept_personal_skill_share(
                share_id="s1", user=USER, session=SESSION
            )
    assert conflict.value.status_code == 409

    with patch.object(
        personal_skills,
        "accept_share",
        new=AsyncMock(side_effect=skill_share_service.SharePermissionError("只有接收人可以接受该分享")),
    ):
        with pytest.raises(HTTPException) as denied:
            await personal_skills.accept_personal_skill_share(
                share_id="s1", user=USER, session=SESSION
            )
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_withdraw_share_maps_not_found():
    with patch.object(
        personal_skills,
        "withdraw_share",
        new=AsyncMock(side_effect=skill_share_service.ShareNotFoundError("share not found")),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await personal_skills.withdraw_personal_skill_share(
                share_id="gone", user=USER, session=SESSION
            )
    assert excinfo.value.status_code == 404
