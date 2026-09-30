"""Langfuse 密钥的加解密。

与 ``app/utils/model_credentials.py`` 保持同一套约定：新写入一律 Fernet 加密并加版本前缀，
读取时兼容历史/手工写入的明文，密文损坏时抛出明确错误而不是静默降级为空密钥。
"""

from typing import Optional

from app.utils.encryption import get_api_key_manager

SECRET_PREFIX = "langfusekey:v1:"


class LangfuseCredentialError(ValueError):
    """Langfuse 密钥密文无法解密时抛出。"""


def encrypt_langfuse_secret(secret: Optional[str]) -> Optional[str]:
    """加密并列化 Langfuse secret key；空值返回 ``None``。"""
    normalized = (secret or "").strip()
    if not normalized:
        return None
    encrypted = get_api_key_manager().encrypt_api_key(normalized)
    return f"{SECRET_PREFIX}{encrypted}"


def decrypt_langfuse_secret(stored_value: Optional[str]) -> Optional[str]:
    """解密 Langfuse secret key，兼容历史明文。"""
    normalized = (stored_value or "").strip()
    if not normalized:
        return None

    if normalized.startswith(SECRET_PREFIX):
        manager = get_api_key_manager()
        try:
            return manager.decrypt_api_key(normalized[len(SECRET_PREFIX):])
        except ValueError as exc:
            raise LangfuseCredentialError(
                "Langfuse Secret Key 密文无法解密，请检查 ENCRYPTION_KEY 或重新录入密钥"
            ) from exc

    # 手工写库或早期版本可能存明文，保持可用。
    return normalized


def is_encrypted_langfuse_secret(stored_value: Optional[str]) -> bool:
    """判断存储值是否为带版本前缀的密文。"""
    return (stored_value or "").strip().startswith(SECRET_PREFIX)
