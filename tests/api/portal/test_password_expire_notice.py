import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from app.api.portal.endpoints.auth import get_current_user_info
from app.models.user import User

@pytest.mark.asyncio
async def test_user_password_expire_notice_calc():
    """测试个人中心用户密码过期周期与提醒计算"""
    mock_db = AsyncMock()
    user_token_payload = {"user_id": "1", "user_name": "admin", "role": "admin"}

    # 1. 用户有密码，10 天前修改过，间隔 30 天 -> 剩 20 天，未过期
    mock_user = MagicMock(spec=User)
    mock_user.id = 1
    mock_user.password_hash = "$2b$12$fakehash"
    mock_user.password_updated_at = datetime.now() - timedelta(days=10)
    mock_user.two_factor_enabled = False
    mock_db.get.return_value = mock_user

    mock_perm = MagicMock()
    mock_perm.permissions.model_dump.return_value = {}

    with patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_get_perm:
        mock_get_perm.return_value = mock_perm
        with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
            mock_config.side_effect = lambda k: "30" if k == "password_expire_days" else None

            res = await get_current_user_info(user=user_token_payload, db=mock_db)
            assert res["status"] == "success"
            pwd_info = res["data"]["password_info"]
            assert pwd_info["has_password"] is True
            assert pwd_info["password_expire_days"] == 30
            assert pwd_info["days_since_last_change"] == 10
            assert pwd_info["days_until_next_change"] == 20
            assert pwd_info["is_expired"] is False

    # 2. 用户有密码，35 天前修改过，间隔 30 天 -> 逾期 5 天，is_expired 为 True
    mock_user.password_updated_at = datetime.now() - timedelta(days=35)
    with patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_get_perm:
        mock_get_perm.return_value = mock_perm
        with patch("app.services.config_service.ConfigService.get", new_callable=AsyncMock) as mock_config:
            mock_config.side_effect = lambda k: "30" if k == "password_expire_days" else None

            res = await get_current_user_info(user=user_token_payload, db=mock_db)
            pwd_info = res["data"]["password_info"]
            assert pwd_info["days_since_last_change"] == 35
            assert pwd_info["days_until_next_change"] == -5
            assert pwd_info["is_expired"] is True
