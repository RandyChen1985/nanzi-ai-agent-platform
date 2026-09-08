import pytest
from fastapi import HTTPException
from app.services.auth_service import AuthService
from app.api.portal.endpoints.auth import PasswordChangeRequest, change_password
from app.api.portal.endpoints.management import SetPasswordRequest, set_user_password
from unittest.mock import AsyncMock, MagicMock, patch

def test_password_complexity_rules():
    """测试等保密码复杂度规则（长度 8-32，必须包含大写/小写/数字/特殊字符中的至少 3 种，无空格，不含用户名）"""
    # 1. 长度不足 8 位
    valid, msg = AuthService.validate_password_complexity("Aa1!")
    assert valid is False
    assert "8 到 32" in msg

    # 2. 长度超过 32 位
    valid, msg = AuthService.validate_password_complexity("A" * 30 + "a1!#$ExtraLongLengthOver32Characters")
    assert valid is False
    assert "8 到 32" in msg

    # 3. 包含空格或不可见空白
    valid, msg = AuthService.validate_password_complexity("Abc 12345!")
    assert valid is False
    assert "空格" in msg

    # 4. 只有小写字母和数字（2 类）
    valid, msg = AuthService.validate_password_complexity("abcdef12345")
    assert valid is False
    assert "必须至少包含" in msg

    # 5. 只有大写字母和数字（2 类）
    valid, msg = AuthService.validate_password_complexity("ABCDEF12345")
    assert valid is False
    assert "必须至少包含" in msg

    # 6. 只有大写字母和小写字母（2 类）
    valid, msg = AuthService.validate_password_complexity("Abcdefghijk")
    assert valid is False
    assert "必须至少包含" in msg

    # 7. 包含 3 类：大写 + 小写 + 数字 -> 合格
    valid, msg = AuthService.validate_password_complexity("Abc12345")
    assert valid is True

    # 8. 包含 3 类：小写 + 数字 + 特殊符号 -> 合格
    valid, msg = AuthService.validate_password_complexity("abc12345@!")
    assert valid is True

    # 9. 包含 3 类：大写 + 数字 + 特殊符号 -> 合格
    valid, msg = AuthService.validate_password_complexity("ABC12345@!")
    assert valid is True

    # 10. 包含 4 类：大写 + 小写 + 数字 + 特殊符号 -> 合格
    valid, msg = AuthService.validate_password_complexity("Abc12345@!")
    assert valid is True

    # 11. 包含用户名（不区分大小写） -> 拒绝
    valid, msg = AuthService.validate_password_complexity("Admin12345!", username="admin")
    assert valid is False
    assert "用户名" in msg

    # 用户名不包含在密码中 -> 合格
    valid, msg = AuthService.validate_password_complexity("SuperSecret123!", username="admin")
    assert valid is True

@pytest.mark.asyncio
async def test_change_password_complexity_endpoint():
    """测试修改个人密码接口的等保复杂度拦截"""
    mock_db = AsyncMock()
    user = {"user_id": "1", "user_name": "alice"}

    # 弱密码被拦截
    with pytest.raises(HTTPException) as exc_info:
        await change_password(
            request=PasswordChangeRequest(password="alice12345"),
            user=user,
            db=mock_db
        )
    assert exc_info.value.status_code == 400

    # 合规密码正常通过
    with patch.object(AuthService, "set_user_password", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = True
        res = await change_password(
            request=PasswordChangeRequest(password="Secr3t!2026"),
            user=user,
            db=mock_db
        )
        assert res["status"] == "success"
        mock_set.assert_called_once()
