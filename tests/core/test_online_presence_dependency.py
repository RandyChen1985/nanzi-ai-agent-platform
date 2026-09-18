"""认证成功后更新在线 Presence 的测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import dependencies


pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_require_api_key_touches_presence_without_changing_auth_result(monkeypatch):
    user_info = {
        "user_id": "7",
        "user_name": "admin",
        "real_name": "管理员",
        "role": "admin",
    }
    verify = AsyncMock(return_value=user_info)
    touch = AsyncMock(return_value=True)
    monkeypatch.setattr(dependencies.AuthService, "verify_api_key", verify)
    monkeypatch.setattr(
        dependencies,
        "OnlinePresenceService",
        SimpleNamespace(touch=touch),
        raising=False,
    )
    request = SimpleNamespace(cookies={}, state=SimpleNamespace())
    response = MagicMock()

    result = await dependencies.require_api_key(
        request,
        response,
        api_key_header="api-key",
        authorization=None,
        db=None,
    )

    assert result["user_id"] == "7"
    assert result["api_key"] == "api-key"
    touch.assert_awaited_once_with(result)
    # header 凭据不得被写进浏览器 Cookie——那等于凭空替调用方建立会话
    response.set_cookie.assert_not_called()


@pytest.mark.asyncio
async def test_presence_failure_does_not_break_authentication(monkeypatch):
    user_info = {"user_id": "7", "user_name": "admin", "role": "admin"}
    monkeypatch.setattr(
        dependencies.AuthService,
        "verify_api_key",
        AsyncMock(return_value=user_info),
    )
    monkeypatch.setattr(
        dependencies,
        "OnlinePresenceService",
        SimpleNamespace(touch=AsyncMock(side_effect=RuntimeError("redis down"))),
        raising=False,
    )
    request = SimpleNamespace(cookies={}, state=SimpleNamespace())

    result = await dependencies.require_api_key(
        request,
        MagicMock(),
        api_key_header="api-key",
        authorization=None,
        db=None,
    )

    assert result["user_id"] == "7"
