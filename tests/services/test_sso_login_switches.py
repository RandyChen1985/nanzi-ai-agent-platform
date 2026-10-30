"""SSO 登录方式开关（SSO_LOGIN_AD / SSO_LOGIN_LOCAL）。

开关是策略不是装饰：被关掉的方式既要让登录页不可选，也要在服务端直接拒绝——
否则绕过页面直接打 `/api/portal/auth/sso/login` 仍然能用。

另一个容易写错的点：默认选中项必须落在可用集合里。把 SSO_LOGIN_DEFAULT_TYPE 配成 ad、
同时关掉 SSO_LOGIN_AD，登录页不能默认高亮一个已经禁用的按钮。
"""

import pytest

from app.services import auth_service

pytestmark = pytest.mark.no_infrastructure


def _set_switches(monkeypatch, ad, local, default):
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_AD", ad)
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_LOCAL", local)
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_DEFAULT_TYPE", default)


@pytest.mark.parametrize(
    "ad, local, default, expected_types, expected_default",
    [
        ("on", "on", "ad", ["ad", "local"], "ad"),
        ("on", "on", "local", ["ad", "local"], "local"),
        # 默认项被关掉 → 回退到仍然可用的那一个
        ("off", "on", "ad", ["local"], "local"),
        ("on", "off", "local", ["ad"], "ad"),
        # 默认项本身写错也同理
        ("on", "off", "ldap", ["ad"], "ad"),
        ("off", "off", "ad", [], None),
        ("off", "off", "local", [], None),
    ],
)
def test_capabilities(monkeypatch, ad, local, default, expected_types, expected_default):
    _set_switches(monkeypatch, ad, local, default)

    caps = auth_service.sso_login_capabilities()

    assert caps["enabled_types"] == expected_types
    assert caps["default_type"] == expected_default


@pytest.mark.parametrize("raw", ["on", "ON", " on ", "1", "true", "yes"])
def test_switch_accepts_truthy_values(raw):
    assert auth_service._switch_on(raw) is True


@pytest.mark.parametrize("raw", ["off", "OFF", "off ", "", None, "0", "false", "no"])
def test_switch_rejects_other_values(raw):
    """只认 on 系写法：env 里写 disabled / enable 这类都不算开着，避免误开。"""
    assert auth_service._switch_on(raw) is False


# --- service 层必须真的拦住 -------------------------------------------------

class _Resp:
    status_code = 200
    text = ""

    def json(self):
        return {"code": 200, "data": False}


def _install_client(monkeypatch, sink):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            sink.append(json)
            return _Resp()

    monkeypatch.setattr(auth_service.httpx, "AsyncClient", _Client)


async def test_disabled_type_is_rejected_without_request(monkeypatch):
    """请求体里点名 ad 也一样被拒——不能靠「前端不给选」当唯一防线。"""
    _set_switches(monkeypatch, "off", "on", "local")
    sent = []
    _install_client(monkeypatch, sent)

    result = await auth_service.AuthService.authenticate_sso_user(
        "alice", "x", login_type="ad"
    )

    assert sent == []
    assert result["status"] == "fail"
    assert result["message"].startswith("该登录方式已被管理员关闭")


async def test_all_disabled_rejects_without_request(monkeypatch):
    _set_switches(monkeypatch, "off", "off", "ad")
    sent = []
    _install_client(monkeypatch, sent)

    result = await auth_service.AuthService.authenticate_sso_user("alice", "x")

    assert sent == []
    assert result["status"] == "fail"
    assert "SSO 登录已关闭" in result["message"]


async def test_enabled_type_still_goes_through(monkeypatch):
    _set_switches(monkeypatch, "off", "on", "local")
    sent = []
    _install_client(monkeypatch, sent)

    await auth_service.AuthService.authenticate_sso_user("alice", "x", login_type="local")

    assert len(sent) == 1
    assert sent[0]["loginType"] == "local"
