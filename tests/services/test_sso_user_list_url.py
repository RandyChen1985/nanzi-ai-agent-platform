"""SSO 用户列表接口的地址必须由 SSO_API_URL 派生。

之前 `https://yovole.net/api/v1/user/list` 是写死在 `get_all_users()` 里的，登录那边
却走 `SSO_API_URL` 配置——同一个 SSO 两个地址来源，换测试环境/私有化部署时必然忘改
其中一处，表现是「登录正常、同步用户超时」。

登录是 `/user/check/login`，列表是 `/user/list`，同域不同 path，所以只能复用
「协议 + 域名 + 网关前缀」，不能直接拿 SSO_API_URL 当列表地址。
"""

import pytest

from app.services.sso_user import LaplacePortalApiClient, build_user_list_url

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.parametrize(
    "api_url, expected",
    [
        # 生产默认值：完整登录地址
        ("https://yovole.net/api/v1/user/check/login", "https://yovole.net/api/v1/user/list"),
        # 只配了根地址
        ("https://yovole.net", "https://yovole.net/api/v1/user/list"),
        ("https://yovole.net/", "https://yovole.net/api/v1/user/list"),
        # 网关把 SSO 挂在子路径下，前缀要保留
        (
            "https://gw.example.com/laplace/api/v1/user/check/login",
            "https://gw.example.com/laplace/api/v1/user/list",
        ),
        # 私网 IP + 端口
        ("http://10.0.0.8:8080/api/v1/user/check/login", "http://10.0.0.8:8080/api/v1/user/list"),
        # 前后空白不该影响结果（.env 里手写很容易带空格）
        ("  https://yovole.net/api/v1/user/check/login  ", "https://yovole.net/api/v1/user/list"),
    ],
)
def test_list_url_follows_sso_api_url(api_url, expected):
    assert build_user_list_url(api_url) == expected


def test_list_url_rejects_relative_config():
    """配成相对路径要当场报错，而不是拼出一个请求不出去的 URL。"""
    with pytest.raises(ValueError):
        build_user_list_url("yovole.net/api/v1/user/list")


def test_list_url_defaults_to_settings():
    """不传参时读 SSO_API_URL，域名与配置保持一致。"""
    from urllib.parse import urlsplit

    from app.core.config import settings

    assert urlsplit(build_user_list_url()).netloc == urlsplit(settings.SSO_API_URL).netloc


def test_get_all_users_posts_to_derived_url(monkeypatch):
    """真正的请求要打到派生出来的地址，而不是写死的那一个。"""
    from app.services import sso_user

    captured = {}

    class _Resp:
        status_code = 200
        content = b'{"data": []}'

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _Resp()

    monkeypatch.setattr(sso_user.requests, "post", fake_post)
    monkeypatch.setattr(
        sso_user, "build_user_list_url", lambda: "https://sso.test.local/api/v1/user/list"
    )

    assert LaplacePortalApiClient.get_all_users() == []
    assert captured["url"] == "https://sso.test.local/api/v1/user/list"
