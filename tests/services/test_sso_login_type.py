"""SSO 登录必须带 `loginType`，并且要把「参数错误」和「口令错误」分开报。

背景：portal-web 的 `check/login` 把 `loginType`（`local` / `ad`）改成了必传项。平台此前
不传这个字段，接口回的是 `HTTP 200 + code=500 + data=false`——旧代码只看 `data`，于是把
「参数缺失」报成「用户名或密码错误」，现场完全无从判断到底是配错还是口令错。

这几个用例守的就是：字段真的发出去了、配置值能被容忍地归一、配错了当场拦下、
以及参数错误别再伪装成口令错误。
"""

import pytest

from app.services import auth_service

pytestmark = pytest.mark.no_infrastructure


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def _install_client(monkeypatch, payload, sink):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            sink["url"] = url
            sink["json"] = json
            return _Resp(payload)

    monkeypatch.setattr(auth_service.httpx, "AsyncClient", _Client)


async def _call(monkeypatch, payload, login_type="ad", requested=None):
    sink = {}
    # 两个开关必须显式钉住：仓库的 .env 里可能配着 SSO_LOGIN_AD=off，
    # 不隔离的话这些用例的结果会随本机环境漂移。
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_AD", "on")
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_LOCAL", "on")
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_DEFAULT_TYPE", login_type)
    _install_client(monkeypatch, payload, sink)
    result = await auth_service.AuthService.authenticate_sso_user(
        "alice", "Init@12345", login_type=requested
    )
    return result, sink


async def test_login_type_is_sent_with_credentials(monkeypatch):
    result, sink = await _call(monkeypatch, {"code": 200, "data": False})

    assert sink["json"]["loginType"] == "ad"
    assert sink["json"]["userName"] == "alice"
    assert sink["json"]["password"] == "Init@12345"
    assert result["status"] == "fail"
    assert result["message"] == "SSO 认证失败: 用户名或密码错误"


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("ad", "ad"),
        ("AD", "ad"),
        (" AD ", "ad"),
        ("local", "local"),
        (" Local ", "local"),
    ],
)
async def test_login_type_is_normalized(monkeypatch, configured, expected):
    _, sink = await _call(monkeypatch, {"code": 200, "data": False}, configured)
    assert sink["json"]["loginType"] == expected


async def test_invalid_env_login_type_falls_back_to_an_enabled_one(monkeypatch):
    """env 写错不能把登录堵死：回退到第一个可用方式，warning 留给运维看。"""
    _, sink = await _call(monkeypatch, {"code": 200, "data": False}, "ldap")

    assert sink["json"]["loginType"] == "ad"


async def test_request_login_type_overrides_env(monkeypatch):
    """前端选了本地密码，就算 env 默认是 ad 也要按前端走。"""
    _, sink = await _call(monkeypatch, {"code": 200, "data": False}, "ad", requested="local")

    assert sink["json"]["loginType"] == "local"


async def test_request_login_type_is_normalized(monkeypatch):
    _, sink = await _call(monkeypatch, {"code": 200, "data": False}, "ad", requested=" LOCAL ")

    assert sink["json"]["loginType"] == "local"


async def test_invalid_request_login_type_fails_instead_of_falling_back(monkeypatch):
    """请求传了非法值不能静默回落到 env：那会让用户以为按自己选的方式登录了。"""
    result, sink = await _call(monkeypatch, {"code": 200, "data": False}, "ad", requested="ldap")

    assert sink == {}
    assert result["status"] == "fail"
    assert "登录方式暂不可用" in result["message"]


def test_schema_accepts_both_login_type_key_styles():
    """后端同时收 login_type（平台风格）和 loginType（portal-web 协议风格）。"""
    from app.api.portal.endpoints.auth import SSOLoginRequest

    snake = SSOLoginRequest.model_validate(
        {"username": "alice", "password": "x", "login_type": "local"}
    )
    camel = SSOLoginRequest.model_validate(
        {"username": "alice", "password": "x", "loginType": "ad"}
    )
    omitted = SSOLoginRequest.model_validate({"username": "alice", "password": "x"})

    assert snake.login_type == "local"
    assert camel.login_type == "ad"
    # 不传由 service 层回落到 SSO_LOGIN_DEFAULT_TYPE，schema 层不设默认值
    assert omitted.login_type is None


@pytest.mark.parametrize(
    "msg",
    [
        "loginType 必传，仅支持 local / ad！",
        "userName 必传！",
        "password 必传！",
    ],
)
async def test_param_error_is_not_reported_as_wrong_password(monkeypatch, msg):
    """HTTP 200 + code=500 是参数错误，不能落进「用户名或密码错误」。"""
    result, _ = await _call(monkeypatch, {"code": 500, "msg": msg, "data": False})

    assert result["status"] == "fail"
    assert "参数" in result["message"]
    assert "用户名或密码错误" not in result["message"]


async def test_http_error_still_reported_separately(monkeypatch):
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_AD", "on")
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_LOCAL", "on")
    monkeypatch.setattr(auth_service.settings, "SSO_LOGIN_DEFAULT_TYPE", "ad")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            return _Resp({}, status_code=502)

    monkeypatch.setattr(auth_service.httpx, "AsyncClient", _Client)

    result = await auth_service.AuthService.authenticate_sso_user("alice", "Init@12345")

    assert result["status"] == "fail"
    assert "502" in result["message"]
