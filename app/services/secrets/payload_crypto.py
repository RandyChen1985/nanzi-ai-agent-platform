"""独立的结构化凭据密钥环。异常不包含载荷、密文或配置值。"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class PayloadCryptoError(ValueError):
    pass


ENABLED_CONFIG_KEY = "structured_credentials_enabled"
KEYRING_CONFIG_KEY = "credential_payload_keyring_json"
PAYLOAD_CONFIG_KEYS = frozenset({ENABLED_CONFIG_KEY, KEYRING_CONFIG_KEY})

_KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,48}\Z")
_KEY_VALUE = re.compile(r"[A-Za-z0-9_-]{43}=\Z")


def _dump(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data, altchars=b"-_", validate=True)


class PayloadCipher:
    def __init__(self, keys: Mapping[str, bytes], active_id: str):
        if active_id not in keys or any(
            not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id) or not isinstance(key, bytes) or len(key) != 32
            for key_id, key in keys.items()
        ):
            raise PayloadCryptoError("结构化凭据密钥环不可用")
        self._keys = dict(keys)
        self.active_id = active_id

    def serialize_with_previous(self, previous_keys: Mapping[str, bytes]) -> str:
        keys = dict(previous_keys)
        for key_id, key in self._keys.items():
            if key_id in keys and keys[key_id] != key:
                raise PayloadCryptoError("请为新密钥更换密钥编号，历史密文仍使用旧编号")
            keys[key_id] = key
        return _dump({
            "active_key_id": self.active_id,
            "keys": {key_id: _b64(key) for key_id, key in keys.items()},
        }).decode("ascii")

    def encrypt(self, value: dict, *, aad: list) -> str:
        nonce = os.urandom(12)
        try:
            sealed = AESGCM(self._keys[self.active_id]).encrypt(
                nonce, _dump(value), _dump(["structured_v1", self.active_id, *aad]),
            )
        except (ValueError, TypeError, UnicodeError):
            raise PayloadCryptoError("结构化凭据加密失败") from None
        return ".".join((self.active_id, _b64(nonce), _b64(sealed)))

    def decrypt(self, envelope: str, *, aad: list) -> dict:
        try:
            key_id, nonce, sealed = envelope.split(".", 2)
            data = AESGCM(self._keys[key_id]).decrypt(
                _unb64(nonce), _unb64(sealed), _dump(["structured_v1", key_id, *aad]),
            )
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (AttributeError, KeyError, ValueError, TypeError, InvalidTag, UnicodeError):
            raise PayloadCryptoError("结构化凭据无法解密") from None


def runtime_child_environment() -> dict[str, str]:
    """密钥环仅供后端使用，子进程不继承结构化凭据密钥。"""
    return {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"CREDENTIAL_PAYLOAD_KEYRING_JSON", "CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID"}
    }


def deployment_keyring_configured() -> bool:
    """检测旧部署配置，包括不完整的密钥环。"""
    from app.core.config import settings

    return bool(settings.CREDENTIAL_PAYLOAD_ACTIVE_KEY_ID) or (
        settings.CREDENTIAL_PAYLOAD_KEYRING_JSON.get_secret_value().strip() not in {"", "{}"}
    )


def previous_payload_keys(raw_keyring: str, *, legacy: bool) -> dict[str, bytes]:
    """保留能解码的旧读密钥，即使旧活跃编号或其他条目失效。"""
    try:
        raw = json.loads(raw_keyring)
        keys = raw if legacy else raw.get("keys")
    except (AttributeError, TypeError, ValueError, RecursionError):
        return {}
    if not isinstance(keys, dict):
        return {}
    previous = {}
    for key_id, value in keys.items():
        if not _KEY_ID.fullmatch(key_id) or not isinstance(value, str):
            continue
        try:
            key = _unb64(value)
        except (ValueError, UnicodeError):
            continue
        if len(key) == 32:
            previous[key_id] = key
    return previous


def parse_payload_keyring(raw_keyring: str, legacy_active_id: str | None = None) -> PayloadCipher:
    try:
        raw = json.loads(raw_keyring)
        if legacy_active_id is None:
            if not isinstance(raw, dict) or raw.keys() != {"active_key_id", "keys"}:
                raise ValueError
            active_id, keys = raw["active_key_id"], raw["keys"]
            if not isinstance(keys, dict) or any(
                not isinstance(value, str) or not _KEY_VALUE.fullmatch(value) for value in keys.values()
            ):
                raise ValueError
        else:
            active_id, keys = legacy_active_id, raw
        if not isinstance(active_id, str) or not isinstance(keys, dict):
            raise ValueError
        return PayloadCipher({key_id: _unb64(value) for key_id, value in keys.items()}, active_id)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise PayloadCryptoError("结构化凭据加密密钥环未配置或无效") from None
