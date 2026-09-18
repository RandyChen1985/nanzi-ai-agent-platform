"""认证 Cookie 的 Secure 属性必须跟随实际请求协议，兼容反向代理终止 TLS。"""

import pytest
from unittest.mock import MagicMock

from app.api.portal.endpoints.auth import _is_secure_request


pytestmark = pytest.mark.no_infrastructure


def _request(scheme: str, forwarded: str | None = None):
    request = MagicMock()
    request.url.scheme = scheme
    request.headers = {} if forwarded is None else {"x-forwarded-proto": forwarded}
    return request


def test_direct_https_request_gets_secure_cookie():
    assert _is_secure_request(_request("https")) is True


def test_plain_http_request_keeps_cookie_usable():
    """纯 HTTP 部署（如 http://ip:8001）必须继续能登录，不能硬编码 Secure。"""
    assert _is_secure_request(_request("http")) is False


def test_forwarded_https_from_reverse_proxy_gets_secure_cookie():
    """k8s ingress / nginx 终止 TLS 时，后端看到的是 http，需信任 X-Forwarded-Proto。"""
    assert _is_secure_request(_request("http", "https")) is True


def test_forwarded_http_is_not_secure():
    assert _is_secure_request(_request("https", "http")) is False


def test_forwarded_proto_header_list_uses_first_hop():
    assert _is_secure_request(_request("http", "https, http")) is True


def test_missing_forwarded_header_falls_back_to_request_scheme():
    assert _is_secure_request(_request("http")) is False
    assert _is_secure_request(_request("https")) is True
