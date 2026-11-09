"""凭据中心：P0 落库止血 + P1 凭据存储与注入。

模块分工：

* :mod:`fingerprint` —— 值指纹（脱敏比对键）
* :mod:`registry`    —— 「平台已知秘密」收集（P0 扫描器用）
* :mod:`scanner`     —— 落库前擦除
* :mod:`guard`       —— 落库路径统一入口
* :mod:`policy`      —— 纯判定（场景 / 域名 / 有效期 / 脚本开关）
* :mod:`resolver`    —— 唯一取值路径（判定的执行者）
* :mod:`center`      —— 写操作（创建 / 轮换 / 绑定 / 授权）

不变式：没有任何函数返回凭据明文——解密结果一律包在 ``SensitiveValue`` 里。

**重模块惰性加载**：``policy`` / ``resolver`` / ``center`` 会拖上 ORM 模型与
数据库会话工厂，而 ``audit.py`` / ``state_store.py`` 只需要 ``guard``。
如果在这里顶层 import，等于让每次写审计都间接加载整套凭据表模型——
启动顺序上也更容易撞上循环导入。所以走 ``__getattr__`` 按需加载。
"""

import importlib

from app.services.secrets.fingerprint import secret_fingerprint
from app.services.secrets.guard import build_storage_scanner, sanitize_for_storage
from app.services.secrets.registry import SecretRegistry, secret_registry
from app.services.secrets.scanner import SecretScanner

_LAZY_SUBMODULES = {
    "center": "app.services.secrets.center",
    "policy": "app.services.secrets.policy",
    "resolver": "app.services.secrets.resolver",
}

_LAZY_ATTRS = {
    "ResolveResult": ("app.services.secrets.resolver", "ResolveResult"),
    "SecretResolver": ("app.services.secrets.resolver", "SecretResolver"),
    "SecretLeakAttempt": ("app.services.secrets.policy", "SecretLeakAttempt"),
    "SensitiveValue": ("app.services.secrets.policy", "SensitiveValue"),
    "secret_resolver": ("app.services.secrets.resolver", "secret_resolver"),
}

__all__ = [
    "ResolveResult",
    "SecretLeakAttempt",
    "SecretRegistry",
    "SecretResolver",
    "SecretScanner",
    "SensitiveValue",
    "build_storage_scanner",
    "center",
    "policy",
    "resolver",
    "sanitize_for_storage",
    "secret_fingerprint",
    "secret_registry",
    "secret_resolver",
]


def __getattr__(name: str):
    if name in _LAZY_SUBMODULES:
        return importlib.import_module(_LAZY_SUBMODULES[name])
    if name in _LAZY_ATTRS:
        module_name, attr = _LAZY_ATTRS[name]
        return getattr(importlib.import_module(module_name), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
