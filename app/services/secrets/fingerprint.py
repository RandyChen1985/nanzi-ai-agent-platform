"""凭据值的指纹计算。

落库前脱敏需要判断「这段文本里有没有某个已知凭据的值」。直接拿明文去比对
当然最准，但明文不该在除了「注入器」以外的任何地方流动，日志和审计里尤其不能出现。

所以这里只留下一个不可逆的短指纹：SHA-256 的前 16 位十六进制。它的用途有两个：

1. 作为落库扫描的比对键（P0 阶段明文仍在内存里过一次，P1 凭据中心上线后改由
   执行器解密、只把指纹交给扫描器，接口不变）。
2. 作为审计里的「命中了哪一条」的可追溯标识——同一个凭据每次算出来都一样，
   但它推不回明文。

16 位（64 bit）足够抵抗碰撞：即使按 10 万条凭据估算，生日碰撞概率也在 1e-10 量级，
远低于误判容忍度。
"""

from __future__ import annotations

import hashlib

#: 指纹取 SHA-256 十六进制摘要的前 N 位。
FINGERPRINT_HEX_LEN = 16


def secret_fingerprint(value: str) -> str:
    """返回凭据值的稳定短指纹；空白值返回空串。"""
    if not value:
        return ""
    normalized = value.strip()
    if not normalized:
        return ""
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_HEX_LEN]
