"""API Key 格式契约：`nzi_` 前缀 + 43 字符随机体。

约定：
- 新生成的 Key 一律以 `nzi_` 开头，便于在日志/抓包中与其它凭据一眼区分
  （`sess_` 门户会话、`emb_ses_` 嵌入会话、`emt_` 嵌入票据）。
- 前缀只是**叠加**，随机部分仍为 `secrets.token_urlsafe(32)`（43 字符 / 256 bit），
  不得为凑长度而减少随机字节。
- **存量兼容**：已发放的无前缀 Key 必须继续可用——`verify_api_key` 不解析格式，
  只做 SHA256 查库，因此加前缀不影响任何既有外部集成。
"""
import secrets
import string

import pytest

from app.utils.encryption import get_api_key_manager

pytestmark = pytest.mark.no_infrastructure

API_KEY_PREFIX = "nzi_"
URLSAFE_ALPHABET = set(string.ascii_letters + string.digits + "-_")


def test_generated_key_has_expected_prefix():
    plaintext, _encrypted, _hashed = get_api_key_manager().generate_api_key()

    assert plaintext.startswith(API_KEY_PREFIX)


def test_generated_key_length_and_charset():
    plaintext, _encrypted, _hashed = get_api_key_manager().generate_api_key()

    # 4 (nzi_) + 43 (token_urlsafe(32))
    assert len(plaintext) == 47
    assert set(plaintext) <= URLSAFE_ALPHABET


def test_random_body_keeps_full_entropy():
    """随机体必须仍是 43 字符，前缀不得挤占随机长度。"""
    plaintext, _encrypted, _hashed = get_api_key_manager().generate_api_key()

    body = plaintext[len(API_KEY_PREFIX):]
    assert len(body) == len(secrets.token_urlsafe(32)) == 43


def test_generated_keys_are_unique():
    keys = {get_api_key_manager().generate_api_key()[0] for _ in range(50)}

    assert len(keys) == 50


def test_hash_and_encryption_cover_the_prefixed_form():
    """哈希与加密必须针对含前缀的完整串，否则校验与回显都会错位。"""
    manager = get_api_key_manager()
    plaintext, encrypted, hashed = manager.generate_api_key()

    assert manager.hash_api_key(plaintext) == hashed
    assert manager.verify_api_key(plaintext, hashed)
    assert manager.decrypt_api_key(encrypted) == plaintext


def test_legacy_unprefixed_key_still_verifies():
    """存量兼容：无前缀的老 Key 依旧能哈希并校验通过。

    这条锁定「加前缀不破坏既有集成」的承诺——外部的 CRM/OA 里配的还是老 Key。
    """
    manager = get_api_key_manager()
    legacy_key = secrets.token_urlsafe(32)  # 旧版本生成的形态：无前缀

    assert not legacy_key.startswith(API_KEY_PREFIX)

    hashed = manager.hash_api_key(legacy_key)
    assert manager.verify_api_key(legacy_key, hashed)


def test_prefixed_and_legacy_keys_coexist():
    """同一套校验逻辑必须同时接受新旧两种形态。"""
    manager = get_api_key_manager()
    new_key, _e, new_hash = manager.generate_api_key()
    legacy_key = secrets.token_urlsafe(32)
    legacy_hash = manager.hash_api_key(legacy_key)

    assert manager.verify_api_key(new_key, new_hash)
    assert manager.verify_api_key(legacy_key, legacy_hash)

    # 交叉校验必须失败：哈希绑定具体字符串，不会张冠李戴
    assert not manager.verify_api_key(new_key, legacy_hash)
    assert not manager.verify_api_key(legacy_key, new_hash)
