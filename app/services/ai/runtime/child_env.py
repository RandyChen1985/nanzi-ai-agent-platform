"""宿主机子进程的最小化环境变量。

``sandbox_policy=local``（平台默认值）时，画布「运行」、对话内 Bash、脚本工具都直接
fork 平台后端的子进程。这些子进程与 FastAPI 主进程**同 UID**，而 ``create_subprocess_*``
在不传 ``env=`` 时会 100% 继承 ``os.environ``——于是数据库口令、Redis 口令、
LLM API Key、``ENCRYPTION_KEY`` 全都落进用户可控的 shell 里，一句 ``env`` 就能读走。

同时 ``app/services/ai/tools/jira_tools.py`` 在模块导入期 ``load_dotenv``，把
``.env`` 的每个键写进 ``os.environ``（``registry.py`` 顶层 import 它），
所以"密钥在环境变量里"不是假设，是既定事实。

因此这里收敛出唯一的环境构造入口：只透传"跑命令真正需要"的少量变量，业务密钥
一律不出场。新增需要透传的变量时请显式加入白名单，不要退化成 ``{**os.environ}``。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

#: 允许透传给用户子进程的环境变量。
#:
#: 判断标准只有一个：**没有它命令就跑不起来，而它本身不携带平台机密**。
#: 刻意不包含任何 ``*_PASSWORD`` / ``*_KEY`` / ``*_SECRET`` / ``*_TOKEN`` /
#: ``DATABASE_URL`` / ``REDIS_*`` —— 这些正是要挡的东西。
PASSTHROUGH_ENV_KEYS = (
    # 进程查找与本地化
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    # 终端能力（交互式命令的 ANSI/宽度探测）
    "TERM",
    "COLUMNS",
    "LINES",
    # Python 运行时：画布跑 python 脚本时要用到
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "PYTHONDONTWRITEBYTECODE",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    # 动态库搜索路径（macOS 的 DYLD_* 缺了会让 .venv 里的解释器直接起不来）
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    # 默认 shell：脚本里 `$SHELL` 很常见，且它只是一个路径，不含机密
    "SHELL",
    # 出网代理：企业环境里 HTTP(S)_PROXY 是"能不能装包/拉仓库"的硬前提，
    # 丢掉它会让 pip/npm/curl/git 全部不通。代理地址不是平台机密。
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    # 自签 CA：内网 HTTPS 必须靠这些才能通过证书校验（否则 requests/curl 直接失败）
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    # 语言工具链根目录：画布允许跑 shell，用户脚本里调 node/java/go/rust 时
    # 少一个就会"命令找不到"。这些只是安装路径，不含凭据。
    "JAVA_HOME",
    "NODE_PATH",
    "NODE_OPTIONS",
    "NVM_DIR",
    "NVM_BIN",
    "GOROOT",
    "GOPATH",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "PYENV_ROOT",
    "PYENV_VERSION",
    # Windows
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
)


def sandbox_child_env(
    *,
    workdir: str | os.PathLike[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """构造用户子进程的环境变量字典。

    Args:
        workdir: 子进程的工作目录；提供时同时作为 ``HOME``，让 ``~``、pip/npm 缓存、
            git 全局配置都落在该用户自己的工作区里，而不是平台后端的 ``HOME``
            （容器内通常是 ``/root``，子进程同 UID 可读其中的 ``.ssh``、``.env``）。
            不提供时**不设** ``HOME``：宁可让少数工具退化（如 ``git`` 找不到全局
            配置），也不把后端 ``HOME`` 或一个可写代码目录伪装成用户家目录。
        extra: 调用方显式追加的变量（如脚本工具注入的凭据），最后覆盖白名单值。

    Returns:
        可直接传给 ``asyncio.create_subprocess_exec(..., env=...)`` 的字典。
    """
    env: dict[str, str] = {}
    for key in PASSTHROUGH_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value

    if not env.get("PATH"):
        env["PATH"] = os.defpath

    if workdir is not None:
        home = os.path.abspath(os.fspath(workdir))
        env["HOME"] = home
        if os.name == "nt":
            env["USERPROFILE"] = home

    if extra:
        for key, value in extra.items():
            if value is None:
                continue
            env[str(key)] = str(value)
    return env


__all__ = ["PASSTHROUGH_ENV_KEYS", "sandbox_child_env"]
