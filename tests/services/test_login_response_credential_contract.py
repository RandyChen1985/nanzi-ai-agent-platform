"""登录 / 画像接口的凭据回传契约（源码级）。

约定：
- 登录响应体**不得**回传真实 API Key。凭据只经 HttpOnly Cookie 下发；一旦回传，
  任何能调用登录接口的人都能取得长期密钥，并可能在日志、代理或抓包中被留存。
- `GET /api/v1/users/profile`（获取用户画像）**需要**返回真实 Key —— 这是业务依赖，
  不得被顺手删掉。
- `POST /api/portal/auth/api-key/reset`（重置）需回传新 Key，否则用户无法取回。
- `GET /api/portal/auth/me` 不得回传凭据（本就逐字段构造，且调用方手上已有凭据）。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUTH_PY = ROOT / "app/api/portal/endpoints/auth.py"
USERS_PY = ROOT / "app/api/v1/endpoints/users.py"

pytestmark = pytest.mark.no_infrastructure


def _function_body(source: str, signature: str) -> str:
    """截取函数体，直到下一个顶层路由装饰器为止。"""
    section = source.split(signature, 1)[1]
    for stop in ("@router.post", "@router.get", "@router.put", "@router.delete", "@router.patch"):
        idx = section.find(stop)
        if idx != -1:
            section = section[:idx]
    return section


@pytest.mark.parametrize(
    "signature",
    ["async def sso_login", "async def login", "async def two_factor_login"],
)
def test_login_response_does_not_return_real_credential(signature):
    """三处登录响应体都不得回传真实 API Key。"""
    body = _function_body(AUTH_PY.read_text(encoding="utf-8"), signature)

    assert '"api_key": api_key' not in body
    assert '"api_key": api_key,' not in body


def test_me_endpoint_does_not_return_credential():
    """当前用户信息接口不得回传凭据。"""
    body = _function_body(AUTH_PY.read_text(encoding="utf-8"), "async def get_current_user_info")

    assert '"api_key"' not in body


def test_profile_endpoint_still_returns_real_credential():
    """获取用户画像需要真实 Key —— 业务依赖，不得被误删。"""
    source = USERS_PY.read_text(encoding="utf-8")

    assert "AuthService.get_decrypted_api_key(user.id, db)" in source
    assert "api_key=api_key" in source


def test_reset_endpoint_still_returns_new_credential():
    """重置接口必须回传新 Key，否则用户无法取回。"""
    body = _function_body(AUTH_PY.read_text(encoding="utf-8"), "async def reset_my_api_key")

    assert '"api_key": new_api_key' in body
