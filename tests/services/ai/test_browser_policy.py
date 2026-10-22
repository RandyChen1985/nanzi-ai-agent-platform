import pytest

from app.services.ai.browser.browser_policy import (
    BrowserUrlBlocked,
    classify_browser_action,
    decide_browser_action,
    redact_browser_arguments,
    validate_browser_navigation,
    validate_browser_request,
)


pytestmark = pytest.mark.no_infrastructure


def test_guarded_blocks_submit_and_allows_search_click():
    assert classify_browser_action(role="button", name="百度一下") == "interact"
    assert classify_browser_action(role="button", name="提交订单") == "commit"
    assert decide_browser_action("guarded", "interact").allowed is True
    assert decide_browser_action("guarded", "commit").requires_confirmation is True


def test_navigation_is_unrestricted():
    """浏览器导航已去掉全部规则：网段、协议、凭据都不再校验，原样返回。"""
    for url in (
        "http://127.0.0.1:8000/",
        "http://10.1.2.168:8111/dashboard/users",
        "http://192.168.1.20/",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8111/",
        "http://user:pass@10.1.2.168:8111/",
        "http://198.18.0.22/",
        "file:///etc/passwd",
    ):
        assert validate_browser_navigation(url) == url


def test_request_guard_is_unrestricted():
    """页面子请求不再被拦截：内网 http、内网 ws、以及任意协议都原样通过。"""
    for url in (
        "ws://127.0.0.1:9222/devtools/browser",
        "http://10.1.2.168:8111/api/portal/management/users",
        "javascript:alert(1)",
        "data:text/plain,ok",
    ):
        assert validate_browser_request(url) == url


def test_public_only_validator_keeps_ssrf_guard_for_server_side_probe():
    from app.services.ai.browser.browser_policy import validate_public_navigation

    with pytest.raises(BrowserUrlBlocked):
        validate_public_navigation("http://127.0.0.1:8000/")
    with pytest.raises(BrowserUrlBlocked):
        validate_public_navigation("http://10.1.2.168:8111/")
    with pytest.raises(BrowserUrlBlocked):
        validate_public_navigation("http://169.254.169.254/latest/meta-data/")
    assert validate_public_navigation("http://198.18.0.22/") == "http://198.18.0.22/"


def test_sensitive_fill_is_redacted_from_audit_payload():
    payload = redact_browser_arguments({"value": "secret", "sensitive": True})
    assert payload["value"] == "<redacted>"
