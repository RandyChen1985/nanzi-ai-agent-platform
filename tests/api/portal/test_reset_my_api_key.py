import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException, Response
from app.api.portal.endpoints.auth import ResetMyApiKeyRequest, reset_my_api_key
from app.services.auth_service import AuthService
from app.services.totp_service import TotpService
from app.models.user import User

@pytest.mark.asyncio
async def test_reset_my_api_key_without_2fa():
    """测试未开启 2FA 用户重置 API Key（必须校验密码）"""
    mock_db = AsyncMock()
    user_info = {"user_id": "10", "user_name": "bob"}
    response = Response()

    # 模拟未开启 2FA 的用户
    mock_user = MagicMock(spec=User)
    mock_user.id = 10
    mock_user.two_factor_enabled = 0
    mock_user.password_hash = "$2b$12$fakehash"
    mock_db.get.return_value = mock_user

    # 1. 未传密码 -> 400
    with pytest.raises(HTTPException) as exc_info:
        await reset_my_api_key(
            request=ResetMyApiKeyRequest(),
            response=response,
            user=user_info,
            db=mock_db
        )
    assert exc_info.value.status_code == 400
    assert "登录密码" in exc_info.value.detail

    # 2. 密码错误 -> 400
    with patch.object(AuthService, "verify_password_hash", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await reset_my_api_key(
                request=ResetMyApiKeyRequest(password="WrongPass123!"),
                response=response,
                user=user_info,
                db=mock_db
            )
        assert exc_info.value.status_code == 400
        assert "登录密码错误" in exc_info.value.detail

    # 3. 密码正确 -> 重置成功
    with patch.object(AuthService, "verify_password_hash", return_value=True):
        with patch.object(AuthService, "reset_api_key", new_callable=AsyncMock, return_value="NEW_API_KEY_123"):
            with patch.object(AuthService, "register_online_state", new_callable=AsyncMock):
                res = await reset_my_api_key(
                    request=ResetMyApiKeyRequest(password="CorrectPass123!"),
                    response=response,
                    user=user_info,
                    db=mock_db
                )
                assert res["status"] == "success"
                assert res["api_key"] == "NEW_API_KEY_123"

@pytest.mark.asyncio
async def test_reset_my_api_key_with_2fa():
    """测试开启 2FA 用户重置 API Key（密码与动态码二选一）"""
    mock_db = AsyncMock()
    user_info = {"user_id": "20", "user_name": "carol"}
    response = Response()

    mock_user = MagicMock(spec=User)
    mock_user.id = 20
    mock_user.two_factor_enabled = 1
    mock_user.password_hash = "$2b$12$fakehash"
    mock_db.get.return_value = mock_user

    # 1. 使用动态码验证成功
    with patch.object(AuthService, "get_user_2fa_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
        with patch.object(TotpService, "verify_code", return_value=True):
            with patch.object(AuthService, "reset_api_key", new_callable=AsyncMock, return_value="NEW_KEY_BY_2FA"):
                with patch.object(AuthService, "register_online_state", new_callable=AsyncMock):
                    res = await reset_my_api_key(
                        request=ResetMyApiKeyRequest(code="123456"),
                        response=response,
                        user=user_info,
                        db=mock_db
                    )
                    assert res["status"] == "success"
                    assert res["api_key"] == "NEW_KEY_BY_2FA"

    # 2. 使用动态码验证失败 -> 400
    with patch.object(AuthService, "get_user_2fa_secret", new_callable=AsyncMock, return_value="JBSWY3DPEHPK3PXP"):
        with patch.object(TotpService, "verify_code", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                await reset_my_api_key(
                    request=ResetMyApiKeyRequest(code="000000"),
                    response=response,
                    user=user_info,
                    db=mock_db
                )
            assert exc_info.value.status_code == 400
            assert "动态验证码错误" in exc_info.value.detail

    # 3. 使用登录密码验证成功（二选一中的密码分支）
    with patch.object(AuthService, "verify_password_hash", return_value=True):
        with patch.object(AuthService, "reset_api_key", new_callable=AsyncMock, return_value="NEW_KEY_BY_PWD"):
            with patch.object(AuthService, "register_online_state", new_callable=AsyncMock):
                res = await reset_my_api_key(
                    request=ResetMyApiKeyRequest(password="CorrectPass123!"),
                    response=response,
                    user=user_info,
                    db=mock_db
                )
                assert res["status"] == "success"
                assert res["api_key"] == "NEW_KEY_BY_PWD"
