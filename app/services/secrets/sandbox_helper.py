"""把「取凭据」这件事放进工作区：一个 helper 命令 + 一张票。

为什么不是环境变量注入：``env`` 一条命令就能把所有变量打出来，而且会继承给
子进程。helper 是**按需取一条**，取完就结束，`env` 里什么也没有。

落盘策略：

* ``.nanzi/config.json``（0600）—— 平台地址等非敏感配置，可长期存在
* ``.nanzi/nanzi-credential``（0600，可执行）—— helper 本体，不含任何明文
* ``.nanzi/ticket``（0600）—— 票据，**每次执行前写、执行后删**

三者都按 0600 建，因为工作区是用户目录的一份挂载：同机其它进程按 uid 读不到，
但同一容器的其它工具能读——票据 TTL 只有 5 分钟，这是刻意接受的窗口。
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

#: 工作区里的隐藏目录名。带点是为了不打扰用户浏览会话文件。
DIR_NAME = ".nanzi"

HELPER_FILENAME = "nanzi-credential"
TICKET_FILENAME = "ticket"
CONFIG_FILENAME = "config.json"

#: helper 本体。只用标准库：沙箱基座是 ``python:3.11-slim``，不保证装了 requests。
#:
#: 它做两件事——``get <key>`` 取一条到 stdout、``list`` 列出可用的标识。
#: 所有错误都写 stderr 并 exit 1，让脚本里 ``$(...)`` 拿到空值、``set -e`` 能中断。
HELPER_SOURCE = '''#!/usr/bin/env python3
"""从平台凭据中心取一条凭据（由平台生成，请勿手改）。

用法：
    KEY=$(nanzi-credential get weather.api.key)
    nanzi-credential list
"""
import json
import os
import sys
import urllib.error
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
_TICKET_FILE = os.path.join(_DIR, "ticket")
_CONFIG_FILE = os.path.join(_DIR, "config.json")


def _die(message):
    sys.stderr.write("错误：%s\\n" % message)
    raise SystemExit(1)


def _config():
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


def _ticket():
    # 票据文件名按 jti 区分（同一会话可能并行跑多条 Bash），平台用环境变量告诉
    # 本次该读哪一个；没有就用默认名，方便手工排查。
    path = os.environ.get("NANZI_TICKET_FILE") or _TICKET_FILE
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _post(path, body):
    base = (os.environ.get("NANZI_PLATFORM_URL") or _config().get("platform_url") or "").rstrip("/")
    if not base:
        _die("平台地址未配置，无法取凭据（请联系平台管理员检查 sandbox_platform_base_url）")
    ticket = _ticket()
    if not ticket:
        _die("票据不存在或已被清理，请重新执行命令")

    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Nanzi-Ticket": ticket},
        method="POST",
    )
    try:
        # **必须绕过代理**：这条请求打的是平台自己的内网地址，走代理不但连不通，
        # 还会把 X-Nanzi-Ticket 交给代理服务器——票据虽短但足以换到凭据。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
        if payload.get("msg"):
            return payload
        return {"code": exc.code, "msg": "平台返回 HTTP %s" % exc.code}
    except urllib.error.URLError as exc:
        _die("连不上平台（%s），请确认沙箱网络可达" % getattr(exc, "reason", exc))
    except (TimeoutError, OSError) as exc:
        # socket 超时不是 URLError，原来会漏到这里直接吐 traceback。
        _die("连接平台超时或被拒绝（%s）" % exc)
    except ValueError:
        _die("平台返回了无法解析的内容")


def _payload(path, body):
    data = _post(path, body)
    if data.get("code") != 0:
        _die(data.get("msg") or "取凭据失败")
    return data.get("data") or {}


def cmd_get(key):
    if not key:
        _die("用法：nanzi-credential get <凭据标识>")
    data = _payload("/internal/script-credential/get", {"key": key})
    value = data.get("value")
    if value is None or value == "":
        _die("凭据 %s 的值为空" % key)
    # 直接写 stdout，不加任何额外字符：$() 会去掉尾部换行，脚本拿到的就是原值。
    sys.stdout.write(str(value))
    if not str(value).endswith("\\n"):
        sys.stdout.write("\\n")


def cmd_list():
    data = _payload("/internal/script-credential/list", {})
    items = data.get("items") or []
    if not items:
        # 平台可能顺带说明「为什么空」（例如两层开关没开）。有就照抄，
        # 没有才是真的没有凭据——这两种情况用户接下来要做的事完全不同。
        hint = data.get("message") or "当前没有可用于脚本的凭据"
        sys.stderr.write(hint + "\\n")
        return
    for item in items:
        key = item.get("key", "")
        name = item.get("name") or ""
        # 名称可能为空（用户没填显示名）：这时只给标识，别留一个孤零零的制表符，
        # 否则粘进脚本/表格里会多一个空列。
        if name:
            sys.stdout.write("%s\\t%s\\n" % (key, name))
        else:
            sys.stdout.write("%s\\n" % key)


def main(argv):
    if len(argv) < 2:
        _die("用法：nanzi-credential <get|list> [凭据标识]")
    action = argv[1]
    if action == "get":
        cmd_get(argv[2] if len(argv) > 2 else "")
    elif action == "list":
        cmd_list()
    else:
        _die("未知命令：%s（可用：get / list）" % action)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


@dataclass(frozen=True)
class CredentialHelper:
    """一次执行期间写下的文件路径。``ticket_path`` 用完必须删。"""

    directory: Path
    helper_path: Path
    ticket_path: Path


async def resolve_platform_url(*, sandboxed: bool) -> str:
    """helper 该连哪个平台地址。

    **必须可配置**：容器里看到的「平台」和平台自己监听的地址不是一回事——
    docker 容器要用 ``host.docker.internal``（Docker Desktop 内置；Linux 需要
    ``--add-host=host.docker.internal:host-gateway``），k8s 要用 service DNS。
    猜错的症状是「连不上」，看着像权限问题，最费时间。

    配置项缺失时才按部署形态推一个默认值，让本地开发开箱可用。
    """
    from app.core.config import settings

    try:
        from app.services.config_service import ConfigService

        configs = await ConfigService.get_all_from_db()
        meta = configs.get("sandbox_platform_base_url") if isinstance(configs, dict) else None
        value = meta.get("value") if isinstance(meta, dict) else meta
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip("/")
    except Exception:  # noqa: BLE001 - 读不到就用推导值，不阻断命令
        pass

    port = getattr(settings, "API_SERVICE_PORT", 8001) or 8001
    host = "host.docker.internal" if sandboxed else "127.0.0.1"
    return f"http://{host}:{port}"


def helper_directory(workdir: str | Path) -> Path:
    return Path(workdir) / DIR_NAME


def _write_private(path: Path, content: str) -> None:
    """以 0600 写文件。已存在时先删再建，避免沿用一个权限更宽的旧 inode。"""
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
            if current == content:
                # 内容没变就只补权限，别为了「原子」去重写一个 0600 的文件。
                if stat.S_IMODE(path.stat().st_mode) != 0o600:
                    os.chmod(path, 0o600)
                return
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass

    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except Exception:
        raise
    os.chmod(path, 0o600)


def write_credential_helper(
    workdir: str | Path,
    *,
    ticket: str,
    platform_url: str,
    ticket_name: str = TICKET_FILENAME,
) -> CredentialHelper:
    """写好 helper、配置与票据，返回三个路径。

    ``ticket_name`` 默认 ``ticket``；带凭据的 Bash 执行会传 ``ticket.<jti>``——
    同一会话并行跑两条命令时，固定文件名会互相覆盖、提前删票，慢的那条中途
    就取不到值了。
    """
    directory = helper_directory(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    # 目录也收紧：FILE 级 0600 挡不住 `ls`，但能挡住别的 uid 列目录内容。
    os.chmod(directory, 0o700)

    helper_path = directory / HELPER_FILENAME
    _write_private(helper_path, HELPER_SOURCE)
    os.chmod(helper_path, 0o700)

    config_path = directory / CONFIG_FILENAME
    _write_private(
        config_path,
        json.dumps({"platform_url": (platform_url or "").rstrip("/")}, ensure_ascii=False),
    )

    ticket_path = directory / ticket_name
    _write_private(ticket_path, ticket)

    return CredentialHelper(
        directory=directory,
        helper_path=helper_path,
        ticket_path=ticket_path,
    )


def remove_ticket_file(ticket_path: str | Path) -> None:
    """删掉**指定的**那张票（按路径，不按目录扫）。

    按路径删而不是「删目录下的 ticket」：并行执行时删错一张就会让另一条命令在
    半途失去凭据。
    """
    try:
        Path(ticket_path).unlink()
    except FileNotFoundError:
        pass


def remove_ticket(workdir: str | Path) -> None:
    """删票。执行完就该删——留在工作区等于给脚本（和任何能读工作区的工具）
    一个长期可用的换取凭证的能力。"""
    path = helper_directory(workdir) / TICKET_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # 删不掉不该影响命令结果，但要在日志里看得出来（调用方负责记）。
        raise
