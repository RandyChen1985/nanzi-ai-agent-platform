"""落库前的凭据擦除器。

现有 ``app.utils.masking`` 解决的是「**键名**像凭据」（``password`` / ``X-Api-Key``）
和「值**看起来**像平台签发的凭据」（``nzi_`` / ``sess_`` 前缀）。它解决不了一类最常见
的泄漏：

.. code-block:: text

    {"error": "connect failed: Access denied for user 'ops' using 'P@ssw0rd!2024'"}

这里的密钥既不在敏感键名下，也不带平台前缀——它是用户在系统配置里填的一个**普通字符串**。
键名规则对此完全失明。

``SecretScanner`` 补的就是这一段：拿「平台已知的凭据值」去做精确子串匹配，命中就替换成
``${secret:<标签>}``。替换成引用而不是 ``******``，是为了让审计读起来仍然有意义——
运维看到 ``${secret:config.mysql_ops_password}`` 就知道「这里原本是一个数据库密码」，
而不是看到一串星号去猜。

两道规则串联执行（先值、后键名），互不吞并：敏感键名的值一律 ``******``，其余位置才由
本扫描器补刀。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.utils.masking import mask_sensitive_data

#: 短于该长度的值不参与替换。``on`` / ``1234`` / ``true`` 这类值在正常文本里到处都是，
#: 参与匹配会把审计内容糟蹋成筛子，而它们的泄漏风险本来也远低于长密钥。
MIN_SECRET_LEN = 8

#: 命中后的替换形态。与凭据中心将来的引用语法保持一致。
MASK_TEMPLATE = "${secret:%s}"

#: 标签只允许安全字符，防止配置 key 里的怪字符破坏 JSON / 正则。
_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_label(label: str) -> str:
    cleaned = _LABEL_UNSAFE.sub("_", (label or "").strip())
    return cleaned or "unknown"


class SecretScanner:
    """把已知凭据值从任意结构里擦掉。"""

    def __init__(self, secrets: Iterable[tuple[str, str]] | None = None) -> None:
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, value in secrets or ():
            if not isinstance(value, str):
                continue
            normalized = value.strip()
            if len(normalized) < MIN_SECRET_LEN or normalized in seen:
                continue
            seen.add(normalized)
            pairs.append((_safe_label(label), normalized))

        # 长值优先替换：否则 "abcdefgh" 会先把 "abcdefghXYZ" 的前半截吃掉，
        # 留下一段既不是原文也不是占位符的残渣。
        pairs.sort(key=lambda item: len(item[1]), reverse=True)
        self._pairs: tuple[tuple[str, str], ...] = tuple(pairs)

    @property
    def is_empty(self) -> bool:
        return not self._pairs

    def mask_text(self, text: str) -> str:
        if not text or not self._pairs:
            return text
        masked = text
        for label, value in self._pairs:
            if value in masked:
                masked = masked.replace(value, MASK_TEMPLATE % label)
        return masked

    def mask_data(self, data: Any) -> Any:
        """递归擦除；最后再交给既有的键名规则收尾。"""
        if data is None:
            return None
        replaced = data if self.is_empty else self._replace_values(data)
        return mask_sensitive_data(replaced)

    def _replace_values(self, data: Any) -> Any:
        if isinstance(data, str):
            return self.mask_text(data)
        if isinstance(data, dict):
            return {key: self._replace_values(value) for key, value in data.items()}
        if isinstance(data, (list, tuple)):
            return [self._replace_values(item) for item in data]
        return data
