"""模型配置页诊断文案的契约测试。"""

from __future__ import annotations

import httpx
import pytest

from app.utils.provider_diagnostics import (
    describe_http_failure,
    describe_invalid_response,
    describe_sdk_failure,
    describe_transport_failure,
    redact_secrets,
)

DASHSCOPE_CATALOG_URL = "https://dashscope.aliyuncs.com/api/v1/deployments/models"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "供应商鉴权失败"),
        (403, "供应商鉴权失败"),
        (404, "接口地址不存在"),
        (429, "触发供应商限流"),
        (502, "供应商服务异常"),
        (400, "供应商拒绝了请求"),
    ],
)
def test_http_failure_maps_status_to_headline(status_code, expected):
    text = describe_http_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        status_code=status_code,
        body='{"code": "InvalidApiKey", "message": "Invalid API-key provided."}',
    )

    assert expected in text
    assert f"HTTP {status_code}" in text
    assert f"请求：GET {DASHSCOPE_CATALOG_URL}" in text
    assert "建议：" in text


def test_http_failure_surfaces_provider_code_and_message():
    text = describe_http_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        status_code=401,
        body='{"code": "InvalidApiKey", "message": "Invalid API-key provided."}',
    )

    assert "InvalidApiKey" in text
    assert "Invalid API-key provided." in text
    assert "dashscope-intl.aliyuncs.com" in text


def test_http_failure_keeps_html_body_readable():
    text = describe_http_failure(
        method="GET",
        request_url="https://example.com/v1/models",
        status_code=400,
        body="<html>\n  <body>blocked</body>\n</html>",
    )

    assert "<html> <body>blocked</body> </html>" in text


def test_http_failure_without_body_still_returns_advice():
    text = describe_http_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        status_code=401,
    )

    assert text.splitlines()[0] == "供应商鉴权失败（HTTP 401）"
    assert "建议：" in text


def test_transport_timeout_mentions_timeout_and_direct_egress():
    text = describe_transport_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        exc=httpx.ConnectTimeout("timed out"),
    )

    assert "超时" in text
    assert "ConnectTimeout: timed out" in text
    assert "HTTP_PROXY" in text


def test_transport_connect_error_points_at_dns_or_firewall():
    text = describe_transport_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        exc=httpx.ConnectError("nodename nor servname provided"),
    )

    assert "无法连接供应商" in text
    assert "DNS" in text
    assert "ConnectError" in text


def test_transport_tls_failure_mentions_certificate():
    text = describe_transport_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        exc=httpx.ConnectError("SSL: CERTIFICATE_VERIFY_FAILED"),
    )

    assert "TLS" in text
    assert "根证书" in text


def test_transport_failure_without_message_still_reports_type():
    text = describe_transport_failure(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        exc=httpx.ReadError(""),
    )

    assert "错误：ReadError" in text
    assert "与供应商的连接被中断" in text


def test_invalid_response_reports_content_type_and_snippet():
    text = describe_invalid_response(
        method="GET",
        request_url=DASHSCOPE_CATALOG_URL,
        body="<html><title>Login</title></html>",
        content_type="text/html; charset=utf-8",
    )

    assert "不是合法 JSON" in text
    assert "text/html" in text
    assert "Login" in text


def test_redact_secrets_masks_api_keys():
    masked = redact_secrets("failed with key sk-abcdef123456 in header")

    assert "sk-abcdef123456" not in masked
    assert "sk-***" in masked


def test_sdk_failure_classifies_authentication_error():
    class AuthenticationError(Exception):
        pass

    text = describe_sdk_failure(AuthenticationError("invalid api key sk-secretvalue123"))

    assert "供应商鉴权失败" in text
    assert "sk-secretvalue123" not in text


def test_sdk_failure_classifies_bad_request_with_parameter_hint():
    class BadRequestError(Exception):
        pass

    text = describe_sdk_failure(
        BadRequestError("enable_thinking is not supported for this model")
    )

    assert "参数不合法" in text
    assert "enable_thinking" in text


def test_sdk_failure_falls_back_for_unknown_errors():
    text = describe_sdk_failure(RuntimeError("boom"))

    assert "调用供应商接口失败" in text
    assert "RuntimeError: boom" in text
