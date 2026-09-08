import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.user import User
from app.services.auth_service import AuthService
from app.api.portal.endpoints.auth import get_current_user_info

@pytest.mark.asyncio
async def test_record_user_login():
    """测试 AuthService.record_user_login 更新用户的 last_login_at"""
    mock_session = AsyncMock()
    mock_user = User(
        id=178,
        user_name="chenxiaolong",
        real_name="陈小龙",
        last_login_at=None
    )
    mock_session.get.return_value = mock_user

    with patch("app.services.auth_service.AuthService._get_session", new_callable=AsyncMock) as mock_get_sess:
        mock_get_sess.return_value = (mock_session, False)
        await AuthService.record_user_login(178, db=mock_session)

        mock_session.get.assert_awaited_once_with(User, 178)
        assert mock_user.last_login_at is not None
        assert isinstance(mock_user.last_login_at, datetime)
        mock_session.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_me_endpoint_returns_last_login_at():
    """测试 /me 接口正常返回 last_login_at 字符串"""
    mock_db = AsyncMock()
    login_time = datetime(2026, 9, 8, 12, 30, 45)
    mock_user_obj = MagicMock(spec=User)
    mock_user_obj.id = 178
    mock_user_obj.password_hash = "mock_hash"
    mock_user_obj.password_updated_at = None
    mock_user_obj.updated_at = None
    mock_user_obj.created_at = datetime(2026, 6, 16, 16, 59, 46)
    mock_user_obj.last_login_at = login_time
    mock_db.get.return_value = mock_user_obj

    current_user_dict = {
        "user_id": "178",
        "user_name": "chenxiaolong",
        "real_name": "陈小龙",
        "role": "user",
        "created_at": "2026-06-16 16:59:46"
    }

    mock_perm_res = MagicMock()
    mock_perm_res.permissions.model_dump.return_value = {"menus": [], "elements": []}

    with patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_perms, \
         patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_cfg, \
         patch("app.services.auth_service.AuthService.get_user_2fa_status", new_callable=AsyncMock) as mock_2fa:
        mock_perms.return_value = mock_perm_res
        mock_cfg.return_value = None
        mock_2fa.return_value = False

        res = await get_current_user_info(user=current_user_dict, db=mock_db)

        assert res["status"] == "success"
        data = res["data"]
        assert data["last_login_at"] == "2026-09-08 12:30:45"
        assert data["user_id"] == "178"
