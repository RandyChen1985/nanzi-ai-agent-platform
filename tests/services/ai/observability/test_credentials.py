"""``credentials`` 单元测试：加密往返、明文兼容、密文损坏。"""

import pytest

from app.services.ai.observability import credentials

pytestmark = pytest.mark.no_infrastructure


def test_encrypt_returns_prefixed_ciphertext():
    stored = credentials.encrypt_langfuse_secret("sk-lf-abc")
    assert stored.startswith(credentials.SECRET_PREFIX)
    assert "sk-lf-abc" not in stored
    assert credentials.decrypt_langfuse_secret(stored) == "sk-lf-abc"


def test_empty_secret_roundtrips_as_none():
    assert credentials.encrypt_langfuse_secret("") is None
    assert credentials.encrypt_langfuse_secret(None) is None
    assert credentials.encrypt_langfuse_secret("   ") is None
    assert credentials.decrypt_langfuse_secret(None) is None
    assert credentials.decrypt_langfuse_secret("   ") is None


def test_secret_is_stripped_before_encryption():
    stored = credentials.encrypt_langfuse_secret("  sk-lf-pad  ")
    assert credentials.decrypt_langfuse_secret(stored) == "sk-lf-pad"


def test_legacy_plaintext_is_accepted():
    assert credentials.decrypt_langfuse_secret("sk-lf-legacy") == "sk-lf-legacy"


def test_broken_ciphertext_raises():
    broken = credentials.SECRET_PREFIX + "not-a-valid-fernet-token"
    with pytest.raises(credentials.LangfuseCredentialError):
        credentials.decrypt_langfuse_secret(broken)


def test_is_encrypted_detects_prefix():
    stored = credentials.encrypt_langfuse_secret("sk-lf-abc")
    assert credentials.is_encrypted_langfuse_secret(stored) is True
    assert credentials.is_encrypted_langfuse_secret("sk-lf-plain") is False
    assert credentials.is_encrypted_langfuse_secret(None) is False
