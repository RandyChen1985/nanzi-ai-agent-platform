import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse
from typing import Optional

class TotpService:
    """
    RFC 6238 标准 TOTP (Time-Based One-Time Password) 服务
    兼容 Google Authenticator, Microsoft Authenticator 等
    完全基于 Python 标准库实现，零外部依赖，高可靠
    """

    @staticmethod
    def generate_secret(byte_length: int = 20) -> str:
        """
        生成 Base32 编码的安全随机密钥 (默认 160-bit / 20 字节)
        """
        raw_bytes = secrets.token_bytes(byte_length)
        return base64.b32encode(raw_bytes).decode("utf-8").rstrip("=")

    @staticmethod
    def generate_otpauth_url(
        user_name: str,
        secret: str,
        issuer: str = "NanZi Platform"
    ) -> str:
        """
        生成 Google Authenticator 标准 otpauth:// 协议 URL
        """
        label = f"{urllib.parse.quote(issuer)}:{urllib.parse.quote(user_name)}"
        params = {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": 6,
            "period": 30,
        }
        return f"otpauth://totp/{label}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _decode_secret(secret: str) -> bytes:
        """
        解码 Base32 密钥，自动补充 padding
        """
        cleaned = secret.strip().replace(" ", "").upper()
        padding_needed = (8 - len(cleaned) % 8) % 8
        padded = cleaned + "=" * padding_needed
        return base64.b32decode(padded, casefold=True)

    @classmethod
    def generate_code(cls, secret: str, timestamp: Optional[int] = None) -> str:
        """
        根据密钥和时间戳生成 6 位 TOTP 动态码 (RFC 6238)
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        counter = timestamp // 30
        key = cls._decode_secret(secret)
        msg = struct.pack(">Q", counter)
        
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        truncated_hash = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        code = truncated_hash % 1_000_000
        return str(code).zfill(6)

    @classmethod
    def verify_code(cls, secret: str, code: str, window: int = 1) -> bool:
        """
        校验动态验证码
        :param secret: Base32 密钥
        :param code: 用户输入的 6 位验证码
        :param window: 容错时间窗口步长 (默认 1，即允许当前时间 ±30 秒共 90 秒的偏差)
        :return: bool 是否匹配
        """
        if not secret or not code:
            return False
        
        code_str = str(code).strip()
        if len(code_str) != 6 or not code_str.isdigit():
            return False

        current_time = int(time.time())
        for step in range(-window, window + 1):
            check_time = current_time + (step * 30)
            try:
                expected_code = cls.generate_code(secret, check_time)
                if hmac.compare_digest(expected_code, code_str):
                    return True
            except Exception:
                continue
        return False
