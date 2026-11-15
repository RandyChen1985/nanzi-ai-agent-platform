"""请求凭据占位符：导出脱敏与运行时兜底共用的一套规则。

为什么单独成模块：``tool_bundle_service``（导出/导入）与 ``generic_api``（运行时）
都要判断「这个 header 是凭据吗」「这个值还是占位符吗」。规则只写一份，才能保证
导出时脱敏掉的、运行时拦下的、页面上标记的是同一个集合。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.services.secrets.references import has_secret_reference

CREDENTIAL_PLACEHOLDER_PREFIX = "${CREDENTIAL:"
CREDENTIAL_PLACEHOLDER_SUFFIX = "}"

#: 名字本身就是凭据的 header（大小写不敏感整名匹配）。
_EXACT_CREDENTIAL_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "www-authenticate"}
)

#: 名字里带这些片段即视为凭据。``token`` 能覆盖 ``X-Auth-Token`` / ``X-Csrf-Token``；
#: 刻意不用裸 ``auth``，否则 ``X-Author`` 之类会被误伤。
_CREDENTIAL_HEADER_KEYWORDS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api-key",
    "apikey",
    "api_key",
    "credential",
    "signature",
)

_HEADER_NAME_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def is_credential_header(name: str) -> bool:
    """header 名是不是凭据。只看名字，不看值——值无法可靠识别。"""
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    if lowered in _EXACT_CREDENTIAL_HEADERS:
        return True
    return any(keyword in lowered for keyword in _CREDENTIAL_HEADER_KEYWORDS)


def credential_placeholder(header_name: str) -> str:
    token = _HEADER_NAME_SAFE_RE.sub("-", str(header_name or "").strip().lower()).strip("-")
    return f"{CREDENTIAL_PLACEHOLDER_PREFIX}{token}{CREDENTIAL_PLACEHOLDER_SUFFIX}"


def is_credential_placeholder(value: Any) -> bool:
    """这个值还是「待填的凭据占位」吗。

    两种写法都算：

    - ``${CREDENTIAL:<header>}``：老形态，值整段来自该槽位绑定的凭据；
    - ``${secret:<key>[.<field>]}``：新形态，可以只取结构化凭据里的某个字段，
      也可能只是前缀拼接（``Bearer ${secret:mail.smtp.token}``）。

    第二种必须一起认——注入层解析失败时会保留原文不动，如果这里判成「用户手填的
    真实值」，那句占位符就会**原样发给外部接口**，用户看到的是对方的 401，
    而不是「引用写错了」。
    """
    if not isinstance(value, str):
        return False
    return value.strip().startswith(CREDENTIAL_PLACEHOLDER_PREFIX) or has_secret_reference(value)


def is_masked_placeholder(value: Any) -> bool:
    """导出/导入专用的**脱敏占位符**，只认 ``${CREDENTIAL:<header>}``。

    和 ``is_credential_placeholder`` 的区别必须留着：后者还要认
    ``${secret:key[.field]}``，那是运行时的「未解析引用」，用它判断「本地值值不值得
    保留」会误伤——用户手写的 ``Bearer ${secret:my.oauth.token}`` 是**有效配置**，
    一次覆盖导入就会把它洗成 ``${CREDENTIAL:authorization}``。
    """
    if not isinstance(value, str):
        return False
    return value.strip().startswith(CREDENTIAL_PLACEHOLDER_PREFIX)


def unbound_reason(value: Any) -> Optional[str]:
    """值里还留着字面量占位符时给出拒绝原因，否则 ``None``。

    两种写法的「该怎么修」不一样，所以原因必须分开：``${secret:key.field}`` 要去
    绑这个槽位（或者字段名写错了），``${CREDENTIAL:header}`` 是根本没绑。
    规则只写一份——注入层和 MCP 建连前的兜底都用它，免得两处口径漂移。
    """
    if not isinstance(value, str):
        return None
    if has_secret_reference(value):
        return "reference_unbound"
    if value.strip().startswith(CREDENTIAL_PLACEHOLDER_PREFIX):
        return "credential_unbound"
    return None


def unresolved_credential_headers(headers: Optional[Mapping[str, Any]]) -> List[str]:
    """headers 里还没填真实值的凭据占位符（导入后未补、或被人手写进去的）。"""
    return [
        str(name)
        for name, value in (headers or {}).items()
        if is_credential_placeholder(value)
    ]


def mask_headers(headers: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, str], List[str]]:
    """返回 ``(脱敏后的 headers, 被替换的 header 名)``。原值不进入返回值。"""
    masked: Dict[str, str] = {}
    requires: List[str] = []
    for raw_name, raw_value in (headers or {}).items():
        name = str(raw_name)
        if is_credential_header(name):
            masked[name] = credential_placeholder(name)
            requires.append(name)
        else:
            masked[name] = "" if raw_value is None else str(raw_value)
    return masked, requires
