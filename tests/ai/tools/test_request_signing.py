"""请求签名 v1：固定向量、规范化与配置校验。"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.models.tool import SysApiTool
from app.services.ai.tools.request_signing import (
    SigningConfigError,
    build_canonical_request,
    canonicalize_query,
    decrypt_signing_secret,
    encrypt_signing_secret,
    parse_signing_config,
    sign_request,
    validate_no_reserved_signing_headers,
)

SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
KEY_ID = "8f3c2a1b7d9e4c60"
TIMESTAMP = "1729334695"
NONCE = "5f2c9a7e4b1d8c3a6e0f2b7d9a4c1e8f"
BODY = b'{"amount":100,"applicant":"alice"}'
BODY_HASH = "191f8fb213eb5569db7324218ab13737cd75320638510f914392ddba0ea1c164"
SIGNATURE = "9cc4a7371ed4f5c9e62baffa7614f688c7fdf3a8e420b97c46ec56aea42168ad"


def test_fixed_vector_matches_spec():
    headers = sign_request(
        method="POST",
        raw_path="/api/orders",
        raw_query="a=1&b=2",
        body=BODY,
        secret=SECRET,
        key_id=KEY_ID,
        timestamp=TIMESTAMP,
        nonce=NONCE,
    )

    assert headers == {
        "X-Nanzi-Signature-Version": "1",
        "X-Nanzi-Key-Id": KEY_ID,
        "X-Nanzi-Timestamp": TIMESTAMP,
        "X-Nanzi-Nonce": NONCE,
        "X-Nanzi-Signature": SIGNATURE,
    }


def test_sign_request_accepts_design_style_url_call():
    headers = sign_request(
        "POST",
        "https://api.test/api/orders?a=1&b=2",
        BODY,
        SECRET,
        KEY_ID,
        TIMESTAMP,
        NONCE,
    )
    assert headers["X-Nanzi-Signature"] == SIGNATURE


def test_canonical_string_hashes_raw_body_and_uses_empty_query_line():
    canonical = build_canonical_request(
        method="get",
        raw_path="/api/users",
        raw_query="",
        body=b"",
        timestamp=TIMESTAMP,
        nonce=NONCE,
        key_id=KEY_ID,
    )

    assert canonical.split("\n")[1:4] == [
        "GET",
        "/api/users",
        "",
    ]
    assert canonical.split("\n")[4] == hashlib.sha256(b"").hexdigest()


def test_canonical_query_sorts_pairs_and_uses_rfc3986():
    assert canonicalize_query("b=2&a=hello+world&a=1") == "a=1&a=hello%20world&b=2"
    assert canonicalize_query("") == ""
    assert canonicalize_query("flag=&flag=1&flag=") == "flag=&flag=&flag=1"
    assert canonicalize_query("name=%E5%BC%A0%E4%B8%89") == "name=%E5%BC%A0%E4%B8%89"


def test_signature_changes_when_body_is_tampered():
    first = sign_request(
        method="POST",
        raw_path="/api/orders",
        raw_query="a=1",
        body=BODY,
        secret=SECRET,
        key_id=KEY_ID,
        timestamp=TIMESTAMP,
        nonce=NONCE,
    )
    second = sign_request(
        method="POST",
        raw_path="/api/orders",
        raw_query="a=1",
        body=b'{"amount":101,"applicant":"alice"}',
        secret=SECRET,
        key_id=KEY_ID,
        timestamp=TIMESTAMP,
        nonce=NONCE,
    )
    assert first["X-Nanzi-Signature"] != second["X-Nanzi-Signature"]


def test_encrypt_and_parse_signing_config_round_trip():
    encrypted = encrypt_signing_secret(SECRET)
    assert encrypted.startswith("sig:v1:")
    assert decrypt_signing_secret(encrypted) == SECRET

    tool = SysApiTool(
        name="signed",
        method="POST",
        url_template="https://api.test/refund",
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_algorithm="hmac-sha256",
        signing_secret_encrypted=encrypted,
    )

    config = parse_signing_config(tool)
    assert config.enabled is True
    assert config.key_id == KEY_ID
    assert config.secret == SECRET


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("signing_secret_encrypted", "", "没有配置签名密钥"),
        ("signing_key_id", "not-a-key", "Key-Id"),
        ("signing_algorithm", "md5", "不支持的签名算法"),
    ],
)
def test_parse_signing_config_fails_closed(field, value, message):
    tool = SysApiTool(
        name="signed",
        method="POST",
        url_template="https://api.test/refund",
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_algorithm="hmac-sha256",
        signing_secret_encrypted=encrypt_signing_secret(SECRET),
    )
    setattr(tool, field, value)

    with pytest.raises(SigningConfigError, match=message):
        parse_signing_config(tool)


def test_reserved_signing_headers_are_rejected_case_insensitively():
    with pytest.raises(SigningConfigError, match="保留前缀"):
        validate_no_reserved_signing_headers({"x-nanzi-signature": "x"})


def test_hmac_uses_secret_utf8_bytes():
    canonical = build_canonical_request(
        method="POST",
        raw_path="/api/orders",
        raw_query="a=1&b=2",
        body=BODY,
        timestamp=TIMESTAMP,
        nonce=NONCE,
        key_id=KEY_ID,
    )
    expected = hmac.new(
        SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert expected == SIGNATURE


@pytest.mark.asyncio
async def test_perform_request_signs_the_final_serialized_request():
    from unittest.mock import AsyncMock, patch

    import httpx

    from app.services.ai.tools.generic_api import GenericApiToolFactory

    config = SysApiTool(
        name="signed",
        method="POST",
        url_template="https://api.test/refund?source=agent",
        headers=json.dumps({"X-Business": "demo"}),
        parameter_schema={"properties": {"amount": {"type": "number"}}},
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_algorithm="hmac-sha256",
        signing_secret_encrypted=encrypt_signing_secret(SECRET),
    )
    capture: dict = {}
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        request.return_value = httpx.Response(200, json={"ok": True})
        await GenericApiToolFactory._perform_request(
            config,
            {"amount": 100},
            capture=capture,
        )
        args, kwargs = request.call_args

    headers = {key.lower(): value for key, value in capture["headers"].items()}
    assert headers["x-nanzi-signature-version"] == "1"
    assert headers["x-nanzi-key-id"] == KEY_ID
    assert headers["x-business"] == "demo"

    url = httpx.URL(args[1])
    canonical = build_canonical_request(
        method=args[0],
        raw_path=url.raw_path.split(b"?", 1)[0].decode("ascii"),
        raw_query=url.query.decode("ascii"),
        body=kwargs["content"],
        timestamp=headers["x-nanzi-timestamp"],
        nonce=headers["x-nanzi-nonce"],
        key_id=headers["x-nanzi-key-id"],
    )
    expected = hmac.new(
        SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert headers["x-nanzi-signature"] == expected


@pytest.mark.asyncio
async def test_perform_request_missing_secret_fails_before_network():
    from unittest.mock import AsyncMock, patch

    from app.services.ai.tools.generic_api import GenericApiToolFactory

    config = SysApiTool(
        name="signed-missing",
        method="POST",
        url_template="https://api.test/refund",
        signing_enabled=True,
        signing_key_id=KEY_ID,
        signing_algorithm="hmac-sha256",
    )
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as request:
        with pytest.raises(SigningConfigError, match="没有配置签名密钥"):
            await GenericApiToolFactory._perform_request(config, {"amount": 1})
    request.assert_not_called()
