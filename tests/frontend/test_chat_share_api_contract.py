"""前端分享 API 层的接线契约：端点、鉴权头、错误分支。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure
SOURCE = "frontend/src/api/chatShare.ts"


def _source() -> str:
    return (ROOT / SOURCE).read_text(encoding="utf-8")


def test_owner_endpoints_use_portal_prefix():
    source = _source()
    assert "/api/portal/chat-shares" in source
    assert "revoke" in source
    # 创建 / 列表 / 作废三条都要带鉴权头注入点
    assert "headers" in source


def test_visitor_endpoints_are_public_and_take_token():
    source = _source()
    assert "/api/v1/share/" in source
    assert "unlock" in source
    assert "content" in source
    assert "X-Share-Token" in source


def test_visitor_calls_bypass_the_global_401_interceptor():
    """访客请求不能走默认 axios 实例。

    `main.ts` 在 axios 默认实例上挂了全局响应拦截器：任何 401 都会清 localStorage
    并整页跳 `/login`。分享页密码输错正是 401，一旦走默认实例，页面刚渲染出
    「密码不正确」就被跳走。故访客必须用 `axios.create()` 出来的独立实例。
    """
    source = _source()
    assert "axios.create(" in source
    # 默认实例上不能出现任何直接调用（有的话就会被全局拦截器接管）
    for call in ("axios.get(", "axios.post(", "axios.put(", "axios.delete("):
        assert call not in source, call
    # 三条访客请求都必须挂在独立实例上
    assert source.count("visitorClient.") >= 3


def test_types_mirror_backend_payload():
    source = _source()
    for field in (
        "access_mode", "expiry", "secret", "share_id", "view_count",
        "last_viewed_at", "expires_at", "turn_count",
    ):
        assert field in source, field
