import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from app.services.totp_service import TotpService
from app.services.auth_service import AuthService
from app.api.portal.endpoints.auth import (
    EnableTwoFactorRequest,
    DisableTwoFactorRequest,
    TwoFactorLoginRequest,
    setup_two_factor,
    enable_two_factor,
    disable_two_factor,
    two_factor_login,
)

def test_totp_service_secret_and_code():
    """测试 TOTP 秘钥生成、动态码生成与时间容错校验"""
    secret = TotpService.generate_secret()
    assert isinstance(secret, str)
    assert len(secret) >= 16
    
    # 动态码格式
    code = TotpService.generate_code(secret)
    assert len(code) == 6
    assert code.isdigit()
    
    # 当前时间校验
    assert TotpService.verify_code(secret, code) is True
    # 错误验证码拒绝
    fake_code = "000000" if code != "000000" else "111111"
    assert TotpService.verify_code(secret, fake_code) is False
    # 非法长度拒绝
    assert TotpService.verify_code(secret, "123") is False
    assert TotpService.verify_code(secret, "abcdef") is False

def test_totp_service_otpauth_url():
    """测试 Google Authenticator 标准 otpauth 协议 URL 生成"""
    secret = "JBSWY3DPEHPK3PXP"
    url = TotpService.generate_otpauth_url("alice", secret, issuer="NanZi Test")
    assert url.startswith("otpauth://totp/")
    assert "secret=JBSWY3DPEHPK3PXP" in url
    assert "NanZi" in url
    assert "alice" in url

@pytest.mark.asyncio
async def test_2fa_setup_flow():
    """测试个人中心发起 2FA 绑定流程"""
    mock_user = {"user_id": "1001", "user_name": "test_user"}
    mock_db = AsyncMock()

    with patch("app.services.auth_service.AuthService.create_2fa_setup_cache", new_callable=AsyncMock) as mock_cache, \
         patch("app.services.branding_settings_service.BrandingSettingsService.get_public_branding", new_callable=AsyncMock) as mock_branding:
        mock_branding.return_value = {"product_name": "南孜平台"}
        res = await setup_two_factor(user=mock_user, db=mock_db)
        
        assert res["status"] == "success"
        assert "secret" in res["data"]
        assert "otpauth_url" in res["data"]
        mock_cache.assert_awaited_once()

@pytest.mark.asyncio
async def test_2fa_enable_flow_success_and_failure():
    """测试确认启用 2FA（校验正确与错误验证码）"""
    mock_user = {"user_id": "1001", "user_name": "test_user"}
    mock_db = AsyncMock()
    secret = TotpService.generate_secret()
    correct_code = TotpService.generate_code(secret)

    # 1. 成功场景
    with patch("app.services.auth_service.AuthService.get_2fa_setup_cache", new_callable=AsyncMock, return_value=secret), \
         patch("app.services.auth_service.AuthService.enable_user_2fa", new_callable=AsyncMock) as mock_enable, \
         patch("app.services.auth_service.AuthService.clear_2fa_setup_cache", new_callable=AsyncMock):
        req = EnableTwoFactorRequest(code=correct_code)
        res = await enable_two_factor(request=req, user=mock_user, db=mock_db)
        assert res["status"] == "success"
        mock_enable.assert_awaited_once_with(1001, secret, db=mock_db)

    # 2. 验证码错误场景
    with patch("app.services.auth_service.AuthService.get_2fa_setup_cache", new_callable=AsyncMock, return_value=secret):
        req_wrong = EnableTwoFactorRequest(code="000000" if correct_code != "000000" else "111111")
        with pytest.raises(HTTPException) as exc_info:
            await enable_two_factor(request=req_wrong, user=mock_user, db=mock_db)
        assert exc_info.value.status_code == 400

@pytest.mark.asyncio
async def test_2fa_disable_flow():
    """测试关闭 2FA 流程（密码校验或动态码校验）"""
    mock_user = {"user_id": "1001", "user_name": "test_user"}
    mock_db = AsyncMock()
    
    mock_user_obj = MagicMock()
    mock_user_obj.two_factor_enabled = True
    mock_user_obj.password_hash = "hashed_pw"
    mock_db.get.return_value = mock_user_obj

    # 使用密码关闭
    with patch("app.services.auth_service.AuthService.verify_password_hash", return_value=True), \
         patch("app.services.auth_service.AuthService.disable_user_2fa", new_callable=AsyncMock) as mock_disable:
        req = DisableTwoFactorRequest(password="secret_pass")
        res = await disable_two_factor(request=req, user=mock_user, db=mock_db)
        assert res["status"] == "success"
        mock_disable.assert_awaited_once_with(1001, db=mock_db)

@pytest.mark.asyncio
async def test_2fa_login_two_step():
    """测试开启 2FA 后的两步登录验证"""
    mock_db = AsyncMock()
    mock_response = MagicMock()

    user_info = {
        "user_id": "1001",
        "user_name": "admin",
        "role": "admin",
        "two_factor_enabled": True
    }

    # 模拟动态码验证成功
    with patch("app.services.auth_service.AuthService.verify_and_consume_2fa_pending_token", new_callable=AsyncMock, return_value=user_info), \
         patch("app.services.auth_service.AuthService.get_decrypted_api_key", new_callable=AsyncMock, return_value="ak_test_123"), \
         patch("app.services.auth_service.AuthService.register_online_state", new_callable=AsyncMock), \
         patch("app.services.permission_service.PermissionService.get_user_permissions", new_callable=AsyncMock) as mock_perm:
        
        mock_perm_res = MagicMock()
        mock_perm_res.permissions.model_dump.return_value = {"menus": ["all"]}
        mock_perm.return_value = mock_perm_res

        req = TwoFactorLoginRequest(two_factor_token="pending_token_xyz", code="123456")
        res = await two_factor_login(request=req, response=mock_response, db=mock_db)
        
        assert res["status"] == "success"
        assert res["data"]["api_key"] == "ak_test_123"
        mock_response.set_cookie.assert_called_once()

@pytest.mark.asyncio
async def test_register_online_state_with_bool_values():
    """测试 register_online_state 兼容包含布尔值和 None 的用户数据"""
    mock_redis = AsyncMock()
    with patch("app.services.auth_service.get_redis", new_callable=AsyncMock, return_value=mock_redis):
        user_data = {
            "user_id": "1001",
            "user_name": "admin",
            "two_factor_enabled": True,
            "is_admin": False,
            "extra": None
        }
        await AuthService.register_online_state("mock_key_abc", user_data)
        
        # 验证 hset 被调用且 mapping 中的所有值均为合法的 string/int/float/bytes，无 bool 和 None
        mock_redis.hset.assert_awaited_once()
        call_args = mock_redis.hset.await_args
        mapping = call_args.kwargs.get("mapping")
        assert mapping is not None
        assert mapping["two_factor_enabled"] == "1"
        assert mapping["is_admin"] == "0"
        assert "extra" not in mapping
        assert mapping["status"] == "1"
        for val in mapping.values():
            assert not isinstance(val, bool)
            assert isinstance(val, (str, int, float, bytes))

