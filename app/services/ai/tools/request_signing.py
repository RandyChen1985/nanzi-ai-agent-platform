"""API 工具请求签名 v1。

协议见 ``docs/superpowers/specs/2026-10-19-api-tool-request-signing-design.md``。
本模块只负责规范化、HMAC 和密钥加解密，不查库、不做权限判断。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import parse_qsl, quote, urlsplit

from app.utils.encryption import get_api_key_manager

SIGNATURE_VERSION = "1"
SIGNATURE_ALGORITHM = "hmac-sha256"
SIGNATURE_PREFIX = "X-Nanzi-"
SIGNATURE_HEADERS = (
    "X-Nanzi-Signature-Version",
    "X-Nanzi-Key-Id",
    "X-Nanzi-Timestamp",
    "X-Nanzi-Nonce",
    "X-Nanzi-Signature",
)
# 设计稿里使用这个更明确的名字；保留短名给内部调用。
SIGNATURE_HEADER_NAMES = SIGNATURE_HEADERS
SECRET_PREFIX = "sig:v1:"

_KEY_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SECRET_RE = re.compile(r"^[\x21-\x7e]{32,128}$")


class SigningConfigError(ValueError):
    """签名配置不合法。

    调用方必须失败关闭：不能因为配置/解密失败就退化成未签名请求。
    """


@dataclass(frozen=True)
class SigningConfig:
    enabled: bool
    key_id: str | None
    algorithm: str
    secret: str | None


def validate_signing_key_id(key_id: str | None) -> str:
    value = str(key_id or "").strip()
    if not _KEY_ID_RE.fullmatch(value):
        raise SigningConfigError("签名 Key-Id 必须是 16 位小写十六进制")
    return value


def validate_signing_secret(secret: str | None) -> str:
    value = str(secret or "")
    if not _SECRET_RE.fullmatch(value):
        raise SigningConfigError("签名密钥必须是 32-128 个可打印 ASCII 字符")
    return value


def encrypt_signing_secret(secret: str) -> str:
    """加密保存签名密钥，带版本前缀，便于后续迁移。"""
    validated = validate_signing_secret(secret)
    try:
        encrypted = get_api_key_manager().encrypt_api_key(validated)
    except Exception as exc:
        # 加密服务不可用时失败关闭：不能把明文写进数据库，也不能静默跳过签名。
        raise SigningConfigError("签名密钥加密失败，请检查平台加密配置") from exc
    return f"{SECRET_PREFIX}{encrypted}"


def decrypt_signing_secret(stored: str) -> str:
    value = str(stored or "")
    if not value.startswith(SECRET_PREFIX):
        raise SigningConfigError("签名密钥存储格式不合法")
    try:
        return get_api_key_manager().decrypt_api_key(value[len(SECRET_PREFIX):])
    except Exception as exc:
        raise SigningConfigError("签名密钥无法解密") from exc


def parse_signing_config(tool: Any) -> SigningConfig:
    """从 SysApiTool（或内存草稿）解析签名配置；关闭时不解密。"""
    enabled = bool(getattr(tool, "signing_enabled", False))
    algorithm = str(getattr(tool, "signing_algorithm", "") or SIGNATURE_ALGORITHM).lower()
    key_id = str(getattr(tool, "signing_key_id", "") or "").strip()
    stored_secret = str(getattr(tool, "signing_secret_encrypted", "") or "").strip()

    if not enabled:
        return SigningConfig(
            enabled=False,
            key_id=key_id or None,
            algorithm=algorithm,
            secret=None,
        )
    if algorithm != SIGNATURE_ALGORITHM:
        raise SigningConfigError(f"不支持的签名算法：{algorithm}")
    validate_signing_key_id(key_id)
    if not stored_secret:
        raise SigningConfigError("已开启请求签名，但没有配置签名密钥")
    secret = validate_signing_secret(decrypt_signing_secret(stored_secret))
    return SigningConfig(
        enabled=True,
        key_id=key_id,
        algorithm=algorithm,
        secret=secret,
    )


def validate_no_reserved_signing_headers(headers: Mapping[str, Any] | None) -> None:
    for key in headers or {}:
        if str(key).lower().startswith(SIGNATURE_PREFIX.lower()):
            raise SigningConfigError(f"普通 Headers 不能使用平台保留前缀：{key}")


def canonicalize_query(raw_query: str) -> str:
    """按解码后的 key/value 排序，再以 RFC3986 编码。

    保留重复 key；`+` 按空格处理，空格最终编码为 `%20`。
    """
    pairs = parse_qsl(raw_query or "", keep_blank_values=True)
    pairs.sort(key=lambda pair: (pair[0].encode("utf-8"), pair[1].encode("utf-8")))
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}"
        for key, value in pairs
    )


def build_canonical_request(
    *,
    method: str,
    raw_path: str | None = None,
    raw_query: str | None = None,
    body: bytes = b"",
    timestamp: str,
    nonce: str,
    key_id: str,
    version: str = SIGNATURE_VERSION,
    path: str | None = None,
    query: str | None = None,
) -> str:
    """构造 8 行签名串。

    参数名同时接受原始请求字段（raw_path/raw_query）和设计稿里的通用叫法
    （path/query），方便业务侧/单测直接调用。
    """
    path_value = raw_path if raw_path is not None else path
    query_value = raw_query if raw_query is not None else query
    if path_value is None:
        raise ValueError("build_canonical_request 需要 raw_path 或 path")
    return "\n".join([
        str(version),
        str(method).upper(),
        str(path_value),
        canonicalize_query(str(query_value or "")),
        hashlib.sha256(body).hexdigest(),
        str(timestamp),
        str(nonce),
        str(key_id),
    ])


def sign_request(
    method: str,
    url: str | None = None,
    body: bytes = b"",
    secret: str = "",
    key_id: str = "",
    timestamp: str | None = None,
    nonce: str | None = None,
    *,
    raw_path: str | None = None,
    raw_query: str | None = None,
    body_bytes: bytes | None = None,
) -> dict[str, str]:
    """生成五个签名 Header。

    支持两种调用方式：
    - 内部内核：sign_request(method=..., raw_path=..., raw_query=..., body=...)
    - 设计稿/外部接入：sign_request("POST", "https://host/api?a=1", body_bytes, secret, key_id)

    timestamp/nonce 可注入，便于固定向量测试。
    """
    if body_bytes is not None:
        body = body_bytes
    if raw_path is None:
        if url is None:
            raise ValueError("sign_request 需要 url 或 raw_path")
        parsed = urlsplit(str(url))
        raw_path = parsed.path or "/"
        if raw_query is None:
            raw_query = parsed.query
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    nonce_value = nonce or secrets.token_hex(16)
    key_id_value = validate_signing_key_id(key_id)
    secret_value = validate_signing_secret(secret)
    canonical = build_canonical_request(
        method=method,
        raw_path=raw_path,
        raw_query=raw_query or "",
        body=body,
        timestamp=timestamp_value,
        nonce=nonce_value,
        key_id=key_id_value,
    )
    signature = hmac.new(
        secret_value.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Nanzi-Signature-Version": SIGNATURE_VERSION,
        "X-Nanzi-Key-Id": key_id_value,
        "X-Nanzi-Timestamp": timestamp_value,
        "X-Nanzi-Nonce": nonce_value,
        "X-Nanzi-Signature": signature,
    }