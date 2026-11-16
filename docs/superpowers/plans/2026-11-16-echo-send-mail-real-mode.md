# Echo send_mail 真实模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让平台内置 Echo MCP 的 `send_mail` 能用**注入进来的** SMTP 参数真连服务器（`verify` 验到登录、`send` 真发一封），从而证明凭据注入「不只是值到了，而且值能用」。

**Architecture:** 在既有 `send_mail` 工具上追加两个参数（`mode` / `body`），`mode` 缺省仍是现在的纯模拟回执。`verify` / `send` 走一条独立支路：参数校验分级 → 出站地址守卫（拒绝回环/私网/保留/云元数据）→ 线程池内用 `smtplib` 跑阶段机（connect / ehlo / starttls / login / sendmail），每阶段记录 SMTP 应答码与耗时，失败不抛异常而是返回 `ok:false + reason`，并保留原有的参数回执。

**Tech Stack:** Python 3.11、FastMCP（`mcp.server.mcpserver`）、`smtplib` / `ssl` / `socket` / `ipaddress`、pytest（`asyncio_mode = auto`）。

**设计文档：** [`docs/superpowers/specs/2026-11-16-echo-send-mail-real-mode-design.md`](../specs/2026-11-16-echo-send-mail-real-mode-design.md)

---

## 执行约定（本仓库专属，覆盖 skill 默认行为）

- **不要自行执行 `git commit`**。每个 Task 末尾列出了建议的 commit 命令，但本仓库 `AGENTS.md` 规定提交权在用户：执行到那里时**停下来向用户汇报**「改了哪些文件、验证结果」，由用户决定提交。
- 不主动执行 `./dev.sh` 或任何服务启停脚本。
- 每个 Task 的测试命令都在仓库根目录、用 `.venv/bin/python -m pytest`。

## File Structure

| 文件 | 职责 | 改动量 |
|---|---|---|
| `app/services/mcp/echo_server.py` | 既有 Echo MCP 定义。本次新增：真实模式的常量、参数校验、地址守卫、阶段机、`mode` 分支；同步 `send_mail_tool_schema()` 与工具描述 | +约 230 行 |
| `tests/services/mcp/test_echo_mail_real_mode.py` | 新增测试文件：参数校验、地址守卫、阶段机三条路径（成功 / 535 认证失败 / 收件人被拒）、`mode` 缺省兼容 | 新建，约 260 行 |
| `tests/secrets/test_locator_e2e.py` | 既有凭据注入 e2e。补一条「`mode` 缺省返回体键集不含 `real`」的断言 | +8 行 |
| `tests/CHECKLIST.md` | 交付登记 | +1 行 |

不新建模块：`echo_server.py` 改造后约 850 行，仍在仓库既有文件规模内（设计文档已定此落点）。

---

## Task 1: 参数校验分级与出站地址守卫

**Files:**
- Modify: `app/services/mcp/echo_server.py`（在 `_receipt()` 之前插入常量与校验函数，约 385 行处）
- Test: `tests/services/mcp/test_echo_mail_real_mode.py`（新建）

- [ ] **Step 1: 写失败测试（新建测试文件）**

```python
"""Echo send_mail 真实模式：参数校验与出站地址守卫。"""

from __future__ import annotations

import pytest

from app.services.mcp import echo_server

pytestmark = pytest.mark.no_infrastructure


class TestValidateRealMailParams:
    def test_accepts_complete_smtp_params(self):
        result = echo_server._validate_real_mail_params(
            to="a@b.com",
            host="smtp.qq.com",
            port=465,
            security="ssl",
            username="ops@b.com",
            password="secret",
        )
        assert result["ok"] is True
        assert result["normalized"]["host"] == "smtp.qq.com"
        assert result["normalized"]["port"] == 465
        assert result["normalized"]["security"] == "ssl"

    def test_missing_password_is_missing_params_not_invalid(self):
        result = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com", port=465, security="ssl",
            username="ops@b.com", password=None,
        )
        assert result["ok"] is False
        assert result["reason"] == "missing_params"
        assert "password" in result["missing"]
        # 文案必须指向「注入没生效」，而不是「凭据错了」
        assert "绑定" in result["message"]

    def test_missing_host_and_username_reports_both(self):
        result = echo_server._validate_real_mail_params(
            to="a@b.com", host=None, port=465, security="ssl",
            username="", password="secret",
        )
        assert result["ok"] is False
        assert sorted(result["missing"]) == ["host", "username"]

    @pytest.mark.parametrize("port", [0, -1, 65536, 99999])
    def test_invalid_port_is_invalid_params(self, port):
        result = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com", port=port, security="ssl",
            username="ops@b.com", password="secret",
        )
        assert result["ok"] is False
        assert result["reason"] == "invalid_params"
        assert str(port) in result["message"]

    def test_invalid_security_is_invalid_params(self):
        result = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com", port=465, security="obsolete",
            username="ops@b.com", password="secret",
        )
        assert result["ok"] is False
        assert result["reason"] == "invalid_params"
        assert "obsolete" in result["message"]

    @pytest.mark.parametrize("to", ["a@b.com,c@d.com", "a@b.com;c@d.com", "no-at-sign", ""])
    def test_invalid_recipient_is_invalid_params(self, to):
        result = echo_server._validate_real_mail_params(
            to=to, host="smtp.qq.com", port=465, security="ssl",
            username="ops@b.com", password="secret",
        )
        assert result["ok"] is False
        assert result["reason"] == "invalid_params"

    def test_host_with_slash_is_invalid(self):
        result = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com/mail", port=465, security="ssl",
            username="ops@b.com", password="secret",
        )
        assert result["ok"] is False
        assert result["reason"] == "invalid_params"

    def test_port_and_security_default_by_port(self):
        """port / security 可缺省：显式 ssl 时端口缺省取 465，否则 587 + starttls。"""
        explicit_ssl = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com", port=None, security="ssl",
            username="ops@b.com", password="secret",
        )
        assert explicit_ssl["normalized"]["port"] == 465
        assert explicit_ssl["normalized"]["security"] == "ssl"

        both_missing = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com", port=None, security=None,
            username="ops@b.com", password="secret",
        )
        assert both_missing["normalized"]["port"] == 587
        assert both_missing["normalized"]["security"] == "starttls"


class TestGuardSmtpHost:
    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254",
         "100.100.100.200", "0.0.0.0", "::1", "fd00::1", "[::1]"],
    )
    async def test_blocks_loopback_private_and_metadata(self, host):
        result = await echo_server._guard_smtp_host(host, 25)
        assert result["ok"] is False
        assert result["reason"] == "blocked_address"
        assert result["blocked"], "必须回显命中的具体地址"
        assert "安全拦截" in result["message"]

    async def test_allows_public_literal_ip(self):
        result = await echo_server._guard_smtp_host("1.1.1.1", 25)
        assert result["ok"] is True
        assert result["reason"] is None

    async def test_domain_resolving_to_private_is_blocked(self, monkeypatch):
        monkeypatch.setattr(
            echo_server, "_resolve_host_addresses", lambda host, port: ["10.1.2.3"]
        )
        result = await echo_server._guard_smtp_host("smtp.example.com", 25)
        assert result["ok"] is False
        assert result["reason"] == "blocked_address"
        assert "10.1.2.3" in result["blocked"]

    async def test_domain_resolving_to_public_is_allowed(self, monkeypatch):
        monkeypatch.setattr(
            echo_server, "_resolve_host_addresses", lambda host, port: ["1.1.1.1"]
        )
        result = await echo_server._guard_smtp_host("smtp.example.com", 25)
        assert result["ok"] is True

    async def test_dns_failure_is_reported(self, monkeypatch):
        import socket

        def boom(host, port):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(echo_server, "_resolve_host_addresses", boom)
        result = await echo_server._guard_smtp_host("no-such-host.invalid", 25)
        assert result["ok"] is False
        assert result["reason"] == "dns_failed"

    async def test_rejects_control_chars_and_empty(self):
        assert (await echo_server._guard_smtp_host("", 25))["reason"] == "invalid_params"
        assert (await echo_server._guard_smtp_host("smtp\r\nqq.com", 25))["reason"] == "invalid_params"
        assert (await echo_server._guard_smtp_host("x" * 300, 25))["reason"] == "invalid_params"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/mcp/test_echo_mail_real_mode.py -v`
Expected: FAIL —— `AttributeError: module 'app.services.mcp.echo_server' has no attribute '_validate_real_mail_params'`

- [ ] **Step 3: 实现常量与校验函数**

在 `app/services/mcp/echo_server.py` 的 `def _receipt(` 之前插入：

```python
REAL_MAIL_MODES = ("verify", "send")
DEFAULT_MAIL_MODE = "mock"
_SMTP_SECURITY_MODES = ("ssl", "starttls", "none")
_SMTP_CONNECT_TIMEOUT = 10
_SMTP_SEND_TIMEOUT = 30
_SMTP_SERVER_TEXT_LIMIT = 300
_MAX_SMTP_HOST_LENGTH = 255
# 与 app/services/mcp/url_policy.py 的 _BLOCKED_IP_NETWORKS 同源，但**策略不同**：
# 那边明确放行私网与 loopback（MCP 服务常部署在内网），SMTP 侧必须更严格。
# 注意 Python 3.11 里 100.100.100.200（阿里云元数据）的 is_private 为 False，
# 所以这些网段必须显式列出，不能只靠 is_private。
_BLOCKED_MAIL_IP_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("0.0.0.0/32"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("100.100.100.200/32"),
    ipaddress.ip_network("168.63.129.16/32"),
    ipaddress.ip_network("fd00:ec2::254/128"),
)


def _normalize_smtp_host(host: Any) -> str:
    """去掉方括号、首尾空白与结尾点号（``[::1]`` / ``smtp.qq.com.`` 都见过）。"""
    text = str(host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    return text.lower().rstrip(".")


def _host_has_illegal_chars(host: str) -> bool:
    """主机名不可能出现的字符：控制字符、空白、路径分隔符、``@``。"""
    for char in host:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            return True
        if char in {" ", "\t", "\r", "\n", "\\", "/", "@"}:
            return True
    return False


def _looks_like_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_blocked_mail_ip(ip: "ipaddress._BaseAddress") -> bool:
    """回环 / 私网 / 链路本地 / 保留 / 组播 / 未指定，以及云元数据地址。"""
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    for network in _BLOCKED_MAIL_IP_NETWORKS:
        # 混版本（IPv4 地址 vs IPv6 网段）的 ``in`` 在 3.11 返回 False，
        # 但显式比版本更清楚，也避免依赖该实现细节。
        if ip.version == network.version and ip in network:
            return True
    return False


def _resolve_host_addresses(host: str, port: int) -> list[str]:
    """同步解析，调用方负责丢线程池。返回去重后的全部 A/AAAA 地址。"""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _validate_real_mail_params(
    *,
    to: Any,
    host: Any,
    port: Any,
    security: Any,
    username: Any,
    password: Any,
) -> dict[str, Any]:
    """真实模式的参数校验分级。

    区分 ``missing_params``（值没到 → 大概率绑定没生效）与 ``invalid_params``
    （值到了但用法不对 → 配置写错），两者排查方向完全不同。
    """
    missing: list[str] = []
    for name, value in (("host", host), ("username", username), ("password", password)):
        if not str(value or "").strip():
            missing.append(name)
    if missing:
        return {
            "ok": False,
            "reason": "missing_params",
            "missing": missing,
            "message": (
                f"真实模式缺少参数：{', '.join(missing)}。"
                "请检查该参数是否已标成「来自凭据」并且绑定已生效"
            ),
        }

    normalized_host = _normalize_smtp_host(host)
    if not normalized_host:
        return {"ok": False, "reason": "invalid_params", "message": "SMTP 主机为空"}
    if len(normalized_host) > _MAX_SMTP_HOST_LENGTH or _host_has_illegal_chars(normalized_host):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"SMTP 主机名不合法：{normalized_host!r}",
        }

    recipient = str(to or "").strip()
    if (
        not recipient
        or "," in recipient
        or ";" in recipient
        or "@" not in recipient
        or any(char.isspace() for char in recipient)
    ):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"收件人不合法：{to!r}。真实模式只支持单个邮箱地址",
        }

    from app.services.email_delivery_service import EmailDeliveryService

    raw_port = port
    if raw_port is None or str(raw_port).strip() == "":
        raw_port = 465 if str(security or "").strip().lower() == "ssl" else 587
    try:
        normalized_port = int(raw_port)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"端口不合法：{port!r}，应为 1-65535 的整数",
        }
    if not 1 <= normalized_port <= 65535:
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"端口不合法：{port!r}，应为 1-65535 的整数",
        }

    raw_security = str(security or "").strip().lower()
    if raw_security and raw_security not in _SMTP_SECURITY_MODES:
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": (
                f"加密方式不合法：{raw_security!r}，"
                f"只支持 {' / '.join(_SMTP_SECURITY_MODES)}"
            ),
        }
    # 缺省时与个人中心「测试发信」同源推断：465 → ssl，其余 → starttls。
    normalized_security = EmailDeliveryService.infer_security(
        {"security": raw_security}, normalized_port
    )

    return {
        "ok": True,
        "reason": "ok",
        "normalized": {
            "host": normalized_host,
            "port": normalized_port,
            "security": normalized_security,
            "username": str(username).strip(),
            "password": str(password),
            "to": recipient,
        },
    }


async def _guard_smtp_host(host: str, port: int) -> dict[str, Any]:
    """出站地址守卫：拦住回环 / 私网 / 保留 / 云元数据地址。

    只看「解析出来的地址」，不看 hostname 语义——SMTP 服务器的域名毫无规律
    （``smtp.内网域名`` 之类），按域名猜必然误伤。DNS 在线程池执行，不阻塞事件循环。
    """
    normalized = _normalize_smtp_host(host)
    if not normalized:
        return {"ok": False, "reason": "invalid_params", "message": "SMTP 主机为空"}
    if len(normalized) > _MAX_SMTP_HOST_LENGTH or _host_has_illegal_chars(normalized):
        return {
            "ok": False,
            "reason": "invalid_params",
            "message": f"SMTP 主机名不合法：{normalized!r}",
        }

    if _looks_like_ip(normalized):
        candidates = [normalized]
        source = "literal"
    else:
        loop = asyncio.get_running_loop()
        try:
            candidates = await loop.run_in_executor(
                None, _resolve_host_addresses, normalized, port
            )
        except (socket.gaierror, OSError) as exc:
            return {
                "ok": False,
                "reason": "dns_failed",
                "message": f"SMTP 主机解析失败：{normalized}（{exc}）",
            }
        source = "dns"
        if not candidates:
            return {
                "ok": False,
                "reason": "dns_failed",
                "message": f"SMTP 主机没有解析出任何地址：{normalized}",
            }

    blocked: list[str] = []
    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            return {
                "ok": False,
                "reason": "dns_failed",
                "message": f"SMTP 主机解析出无法识别的地址：{candidate}",
            }
        if _is_blocked_mail_ip(ip):
            blocked.append(candidate)
    if blocked:
        return {
            "ok": False,
            "reason": "blocked_address",
            "blocked": blocked,
            "message": (
                f"SMTP 主机指向内网 / 回环 / 保留地址（{', '.join(blocked)}），"
                "真实模式已拒绝连接；这是安全拦截，不是凭据问题"
            ),
        }

    return {"ok": True, "reason": None, "addresses": candidates, "source": source}
```

同时在文件顶部 import 区补：

```python
import ipaddress
import smtplib
import socket
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr
```

并把 `from typing import Any` 改成 `from typing import Any, Literal`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/mcp/test_echo_mail_real_mode.py -v`
Expected: PASS（约 24 项）

- [ ] **Step 5: 向用户汇报，等待提交指示**

改了什么：`echo_server.py` 新增真实模式常量、参数校验分级、地址守卫。
验证：上述 pytest 命令全绿。
建议 commit（由用户执行）：

```bash
git add app/services/mcp/echo_server.py tests/services/mcp/test_echo_mail_real_mode.py
git commit -m "feat(echo): send_mail 真实模式的参数校验与出站地址守卫"
```

---

## Task 2: SMTP 阶段机（连接 / 登录 / 发送）

**Files:**
- Modify: `app/services/mcp/echo_server.py`（紧随 Task 1 的函数之后）
- Test: `tests/services/mcp/test_echo_mail_real_mode.py`（追加）

- [ ] **Step 1: 写失败测试（追加到测试文件末尾）**

```python
class FakeSmtpServer:
    """进程内最小 SMTP 服务器：只实现真实模式用到的命令。

    只 advertise ``AUTH LOGIN``（不 advertise PLAIN / CRAM-MD5），这样
    ``smtplib.login()`` 必然走 LOGIN 分支，假服务器的状态机才可控。
    """

    def __init__(self, *, auth_ok: bool = True, reject_recipient: bool = False) -> None:
        self.auth_ok = auth_ok
        self.reject_recipient = reject_recipient
        self.messages: list[str] = []
        self.commands: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = int(self._sock.getsockname()[1])
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "FakeSmtpServer":
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _serve(self) -> None:
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(10)
            stream = conn.makefile("rwb", buffering=0)

            def send(text: str) -> None:
                stream.write((text + "\r\n").encode())

            send("220 fake.local ESMTP")
            in_data = False
            buffer: list[str] = []
            while True:
                line = stream.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if in_data:
                    if text == ".":
                        in_data = False
                        self.messages.append("\n".join(buffer))
                        buffer.clear()
                        send("250 2.0.0 Ok: queued")
                    else:
                        buffer.append(text)
                    continue
                self.commands.append(text)
                upper = text.upper()
                if upper.startswith("EHLO") or upper.startswith("HELO"):
                    send("250-fake.local\r\n250-AUTH LOGIN\r\n250 SIZE 10485760")
                elif upper.startswith("AUTH LOGIN"):
                    send("334 VXNlcm5hbWU6")
                    stream.readline()
                    send("334 UGFzc3dvcmQ6")
                    stream.readline()
                    send("235 2.7.0 Authentication successful" if self.auth_ok
                         else "535 5.7.8 Authentication credentials invalid")
                elif upper.startswith("MAIL FROM"):
                    send("250 2.1.0 Ok")
                elif upper.startswith("RCPT TO"):
                    send("550 5.1.1 Recipient address rejected" if self.reject_recipient
                         else "250 2.1.5 Ok")
                elif upper == "DATA":
                    send("354 End data with <CR><LF>.<CR><LF>")
                    in_data = True
                elif upper == "QUIT":
                    send("221 2.0.0 Bye")
                    return
                else:
                    send("250 2.0.0 Ok")


def _allow_loopback(monkeypatch):
    """假 SMTP 监听 127.0.0.1，守卫会拦；测试里把守卫替身成放行。"""

    async def _allow(host: str, port: int) -> dict:
        return {"ok": True, "reason": None, "addresses": ["127.0.0.1"], "source": "dns"}

    monkeypatch.setattr(echo_server, "_guard_smtp_host", _allow)


class TestSmtpTransaction:
    def _run(self, **overrides):
        kwargs = {
            "host": "127.0.0.1",
            "port": 2525,
            "security": "none",
            "username": "ops@b.com",
            "password": "secret",
            "to": "a@b.com",
            "subject": "注入验收",
            "body": "hello",
            "from_name": "运维",
            "mode": "verify",
        }
        kwargs.update(overrides)
        return echo_server._smtp_transaction(**kwargs)

    def test_verify_stops_after_login(self):
        with FakeSmtpServer() as server:
            result = self._run(port=server.port)
        assert result["ok"] is True
        assert result["stage"] == "authenticated"
        assert result["reason"] == "ok"
        assert server.messages == [], "verify 不得发出任何邮件"
        assert not any(cmd.upper().startswith("DATA") for cmd in server.commands)

    def test_send_actually_delivers_one_message(self):
        # 用 ASCII 主题：中文会被 email 头编码成 =?utf-8?b?...?=，正文断言也更直接
        with FakeSmtpServer() as server:
            result = self._run(port=server.port, mode="send", subject="echo-probe")
        assert result["ok"] is True
        assert result["stage"] == "sent"
        assert len(server.messages) == 1
        assert "Subject: echo-probe" in server.messages[0]
        assert "hello" in server.messages[0]

    def test_auth_failure_is_reported_with_code(self):
        with FakeSmtpServer(auth_ok=False) as server:
            result = self._run(port=server.port)
        assert result["ok"] is False
        assert result["stage"] == "login"
        assert result["reason"] == "auth_failed"
        assert result["code"] == 535
        assert "注入链路是通的" in result["message"]

    def test_rejected_recipient_is_send_rejected(self):
        with FakeSmtpServer(reject_recipient=True) as server:
            result = self._run(port=server.port, mode="send")
        assert result["ok"] is False
        assert result["stage"] == "sendmail"
        assert result["reason"] == "send_rejected"
        assert result["code"] == 550

    def test_connection_refused_is_connect_failed(self):
        # 先占一个端口再立刻关掉，避免撞上别的进程
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        result = self._run(port=dead_port)
        assert result["ok"] is False
        assert result["reason"] in {"connect_failed", "timeout"}
        assert result["duration_ms"] >= 0

    def test_password_never_leaks_into_result(self):
        with FakeSmtpServer(auth_ok=False) as server:
            result = self._run(port=server.port)
        assert "secret" not in json.dumps(result, ensure_ascii=False)


class TestSendMailRealBranch:
    async def test_verify_success_keeps_receipt_and_reports_stage(self, monkeypatch):
        _allow_loopback(monkeypatch)
        with FakeSmtpServer() as server:
            result = await echo_server.send_mail(
                None, "a@b.com", "注入验收",
                "127.0.0.1", server.port, "none", "ops@b.com", "secret",
                "运维", None,
                mode="verify",
            )
        assert result["ok"] is True
        assert result["mode"] == "verify"
        assert result["real"]["attempted"] is True
        assert result["real"]["stage"] == "authenticated"
        # 回执仍在：证明「值到没到」与「值能不能用」同时可见
        assert result["smtp"]["host"]["value"] == "127.0.0.1"
        assert result["smtp"]["password"]["fingerprint"]

    async def test_missing_params_does_not_fall_back_to_mock(self, monkeypatch):
        _allow_loopback(monkeypatch)
        result = await echo_server.send_mail(
            None, "a@b.com", "", "127.0.0.1", 2525, "none", "ops@b.com", None,
            None, None,
            mode="verify",
        )
        assert result["ok"] is False
        assert result["mode"] == "verify"
        assert result["real"]["attempted"] is False
        assert result["real"]["reason"] == "missing_params"
        assert "已模拟" not in result["message"]

    async def test_blocked_address_reports_hits(self, monkeypatch):
        result = await echo_server.send_mail(
            None, "a@b.com", "", "10.0.0.5", 25, "none", "ops@b.com", "secret",
            None, None,
            mode="verify",
        )
        assert result["ok"] is False
        assert result["real"]["reason"] == "blocked_address"
        assert result["real"]["attempted"] is False
        assert result["real"]["blocked"] == ["10.0.0.5"]

    async def test_default_mode_keeps_mock_response_shape(self):
        result = await echo_server.send_mail(None, "a@b.com")
        assert result["ok"] is True
        assert set(result) == {"ok", "to", "subject", "smtp", "mailkey", "message"}
        assert "real" not in result
        assert result["message"] == "已模拟给 a@b.com 发送邮件"
```

测试文件顶部需要补 `import json`、`import socket`、`import threading`。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/mcp/test_echo_mail_real_mode.py -k "SmtpTransaction or RealBranch" -v`
Expected: FAIL —— `has no attribute '_smtp_transaction'`

- [ ] **Step 3: 实现阶段机与 send_mail 分支**

在 `_guard_smtp_host` 之后追加：

```python
def _build_real_mail_message(
    *, to: str, subject: str, body: str, username: str, from_name: str
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((from_name, username)) if from_name else username
    message["To"] = to
    message["Subject"] = subject or "NanZi Echo 注入验收"
    message.set_content(body)
    return message


def _build_default_mail_body(
    *, mode: str, host: str, port: int, security: str, username: str, password: str
) -> str:
    fingerprint = hashlib.sha256(password.encode("utf-8")).hexdigest()[:12]
    return "\n".join(
        [
            "这是一封由 NanZi 平台 Echo 测试 MCP 发出的真实测试邮件。",
            "",
            f"模式：{mode}",
            f"SMTP：{host}:{port}（{security}）",
            f"登录账号：{username}",
            f"密码指纹：{fingerprint}（明文不会被回显）",
            "",
            "收到这封邮件说明：凭据注入链路与 SMTP 认证都是真实可用的。",
        ]
    )


def _smtp_transaction(
    *,
    host: str,
    port: int,
    security: str,
    username: str,
    password: str,
    to: str,
    subject: str,
    body: str,
    from_name: str,
    mode: str,
) -> dict[str, Any]:
    """阻塞式阶段机，调用方必须丢线程池。

    每个阶段先落 ``stage`` 再执行，异常时按「当前阶段 + 异常类型」映射 reason，
    这样 ``login`` 失败（凭据错）与 ``connect`` 失败（网络错）能被明确区分。
    """
    from app.services.email_delivery_service import EmailDeliveryService

    def mask(text: Any) -> str:
        return EmailDeliveryService.sanitize_error_text(str(text or ""), password)

    started = time.monotonic()
    state: dict[str, Any] = {"stage": "connect", "code": None, "server": ""}
    connection: smtplib.SMTP | None = None

    def finish(ok: bool, reason: str, message: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "reason": reason,
            "message": message,
            "stage": state["stage"],
            "code": state["code"],
            "server": str(state["server"])[:_SMTP_SERVER_TEXT_LIMIT],
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        context = ssl.create_default_context()
        if security == "ssl":
            connection = smtplib.SMTP_SSL(
                host, port, timeout=_SMTP_CONNECT_TIMEOUT, context=context
            )
        else:
            connection = smtplib.SMTP(host, port, timeout=_SMTP_CONNECT_TIMEOUT)

        state["stage"] = "ehlo"
        code, server_text = connection.ehlo()
        state["code"], state["server"] = code, server_text

        if security == "starttls":
            state["stage"] = "starttls"
            code, server_text = connection.starttls(context=context)
            state["code"], state["server"] = code, server_text
            code, server_text = connection.ehlo()
            state["code"], state["server"] = code, server_text

        state["stage"] = "login"
        code, server_text = connection.login(username, password)
        state["code"], state["server"] = code, server_text

        if mode == "verify":
            state["stage"] = "authenticated"
            # QUIT 失败不该把「已认证成功」翻成失败，忽略即可（finally 会 close）
            try:
                connection.quit()
            except Exception:
                pass
            return finish(
                True,
                "ok",
                "真实校验通过：已用注入参数登录成功（未发送邮件）",
            )

        state["stage"] = "sendmail"
        connection.settimeout(_SMTP_SEND_TIMEOUT)
        message = _build_real_mail_message(
            to=to, subject=subject, body=body, username=username, from_name=from_name
        )
        connection.sendmail(username, [to], message.as_string())
        state["stage"] = "sent"
        try:
            connection.quit()
        except Exception:
            pass
        return finish(True, "ok", f"已用注入参数真实发送 1 封邮件到 {to}")

    except smtplib.SMTPAuthenticationError as exc:
        state["code"] = exc.smtp_code
        state["server"] = mask(exc.smtp_error)
        return finish(
            False,
            "auth_failed",
            "真实校验失败：SMTP 认证被拒。注入链路是通的（主机与账号已到达），"
            "请检查凭据里的账号或授权码是否有效",
        )
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
        state["code"] = getattr(exc, "smtp_code", None)
        state["server"] = mask(getattr(exc, "smtp_error", exc))
        return finish(
            False,
            "send_rejected",
            f"邮件被服务器拒绝（{state['code'] or '无应答码'}）：{state['server']}",
        )
    except ssl.SSLCertVerificationError as exc:
        return finish(False, "cert_error", f"TLS 证书校验失败：{mask(exc)}")
    except smtplib.SMTPServerDisconnected as exc:
        return finish(False, "connect_failed", f"SMTP 服务器提前断开连接：{mask(exc)}")
    except smtplib.SMTPResponseException as exc:
        state["code"] = getattr(exc, "smtp_code", None)
        state["server"] = mask(getattr(exc, "smtp_error", exc))
        reason = "auth_failed" if state["stage"] == "login" else "send_rejected"
        return finish(False, reason, f"SMTP 拒绝（{state['code']}）：{state['server']}")
    except (socket.timeout, TimeoutError) as exc:
        return finish(False, "timeout", f"SMTP 在 {state['stage']} 阶段超时：{mask(exc)}")
    except socket.gaierror as exc:
        return finish(False, "dns_failed", f"SMTP 主机解析失败：{mask(exc)}")
    except OSError as exc:
        return finish(False, "connect_failed", f"无法连接 SMTP 服务器：{mask(exc)}")
    except Exception as exc:  # 兜底：绝不把异常抛给模型侧，否则拿不到回执
        return finish(False, "connect_failed", f"SMTP 调用异常：{mask(exc)}")
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
```

再把 `send_mail` 改造为（保留全部既有参数与回执，新参数追加在末尾）：

```python
@echo_mcp.tool(name=ECHO_MAIL_TOOL_LOCAL_NAME, description=ECHO_MAIL_TOOL_DESCRIPTION)
async def send_mail(
    ctx: Context,
    to: str,
    subject: str = "",
    host: str | None = None,
    port: int | None = None,
    security: str | None = None,
    username: str | None = None,
    password: str | None = None,
    from_name: str | None = None,
    mailkey: str | None = None,
    mode: Literal["mock", "verify", "send"] = DEFAULT_MAIL_MODE,
    body: str | None = None,
) -> dict[str, Any]:
    """模拟 / 真实校验 / 真实发送邮件，并回显每个 SMTP 参数的到达情况。"""

    base: dict[str, Any] = {
        "to": to,
        "subject": subject,
        "smtp": {
            "host": _receipt("host", host, sensitive=False),
            "port": _receipt("port", port, sensitive=False),
            "security": _receipt("security", security, sensitive=False),
            "username": _receipt("username", username, sensitive=False),
            "password": _receipt("password", password, sensitive=True),
            "from_name": _receipt("from_name", from_name, sensitive=False),
        },
        "mailkey": _receipt("mailkey", mailkey, sensitive=True),
    }

    normalized_mode = str(mode or DEFAULT_MAIL_MODE).strip().lower()
    if normalized_mode not in (DEFAULT_MAIL_MODE, *REAL_MAIL_MODES):
        real = {
            "attempted": False,
            "host": host,
            "port": port,
            "security": security,
            "reason": "invalid_params",
        }
        return {
            "ok": False,
            "mode": normalized_mode,
            **base,
            "real": real,
            "message": (
                f"mode 不合法：{normalized_mode!r}，"
                f"只支持 {DEFAULT_MAIL_MODE} / {' / '.join(REAL_MAIL_MODES)}"
            ),
        }

    if normalized_mode == DEFAULT_MAIL_MODE:
        # 键集必须与改造前完全一致：既有凭据注入验收测试依赖它。
        return {"ok": True, **base, "message": f"已模拟给 {to} 发送邮件"}

    validation = _validate_real_mail_params(
        to=to, host=host, port=port, security=security, username=username, password=password
    )
    real: dict[str, Any] = {
        "attempted": False,
        "host": host,
        "port": port,
        "security": security,
    }
    if not validation["ok"]:
        real["reason"] = validation["reason"]
        return {
            "ok": False,
            "mode": normalized_mode,
            **base,
            "real": real,
            "message": validation["message"],
        }

    normalized = validation["normalized"]
    guard = await _guard_smtp_host(normalized["host"], normalized["port"])
    if not guard["ok"]:
        real.update(
            {
                "host": normalized["host"],
                "port": normalized["port"],
                "security": normalized["security"],
                "reason": guard["reason"],
                "blocked": guard.get("blocked", []),
            }
        )
        return {
            "ok": False,
            "mode": normalized_mode,
            **base,
            "real": real,
            "message": guard["message"],
        }

    real.update(
        {
            "attempted": True,
            "host": normalized["host"],
            "port": normalized["port"],
            "security": normalized["security"],
        }
    )
    mail_body = body or _build_default_mail_body(
        mode=normalized_mode,
        host=normalized["host"],
        port=normalized["port"],
        security=normalized["security"],
        username=normalized["username"],
        password=normalized["password"],
    )
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        functools.partial(
            _smtp_transaction,
            host=normalized["host"],
            port=normalized["port"],
            security=normalized["security"],
            username=normalized["username"],
            password=normalized["password"],
            to=normalized["to"],
            subject=subject,
            body=mail_body,
            from_name=str(from_name or "").strip(),
            mode=normalized_mode,
        ),
    )
    real.update(
        {
            "stage": result["stage"],
            "code": result["code"],
            "server": result["server"],
            "duration_ms": result["duration_ms"],
            "reason": result["reason"],
        }
    )
    return {
        "ok": result["ok"],
        "mode": normalized_mode,
        **base,
        "real": real,
        "message": result["message"],
    }
```

补 `import functools`。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/mcp/test_echo_mail_real_mode.py -v`
Expected: PASS（Task 1 + Task 2 全部用例）

- [ ] **Step 5: 向用户汇报，等待提交指示**

建议 commit（由用户执行）：

```bash
git add app/services/mcp/echo_server.py tests/services/mcp/test_echo_mail_real_mode.py
git commit -m "feat(echo): send_mail 支持 verify/send 真实 SMTP 校验"
```

---

## Task 3: 工具描述与稳定 schema 同步

**Files:**
- Modify: `app/services/mcp/echo_server.py:40-47`（`ECHO_MAIL_TOOL_DESCRIPTION`）与 `send_mail_tool_schema()`
- Test: `tests/services/mcp/test_echo_mail_real_mode.py`（追加）

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestSchemaAndRegistration:
    def test_auto_generated_schema_exposes_mode_and_body(self):
        """模型看到的参数表来自 FastMCP 按签名自动生成，必须带 mode / body。"""
        tools = echo_server.echo_mcp._tool_manager.list_tools()
        send_mail_tool = next(
            item for item in tools if getattr(item, "name", None) == "send_mail"
        )
        properties = send_mail_tool.parameters["properties"]
        assert "mode" in properties
        assert set(properties["mode"]["enum"]) == {"mock", "verify", "send"}
        assert "body" in properties
        # 新参数必须追加在末尾，既有位置参数语义不变
        assert list(properties)[-2:] == ["mode", "body"]

    def test_stable_schema_copy_matches_signature(self):
        schema = json.loads(echo_server.send_mail_tool_schema())
        assert set(schema["properties"]["mode"]["enum"]) == {"mock", "verify", "send"}
        assert "body" in schema["properties"]
        assert schema["required"] == ["to"]

    def test_description_warns_default_is_mock(self):
        assert "mock" in echo_server.ECHO_MAIL_TOOL_DESCRIPTION
        assert "verify" in echo_server.ECHO_MAIL_TOOL_DESCRIPTION
        assert "send" in echo_server.ECHO_MAIL_TOOL_DESCRIPTION
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/services/mcp/test_echo_mail_real_mode.py -k Schema -v`
Expected: FAIL —— `KeyError: 'mode'`

- [ ] **Step 3: 更新描述与 schema**

把 `ECHO_MAIL_TOOL_DESCRIPTION` 替换为：

```python
ECHO_MAIL_TOOL_DESCRIPTION = (
    "测试邮件工具，三档 ``mode``：默认 ``mock`` 只回显参数（不发信）；"
    "``verify`` 用注入的 SMTP 参数真连并登录（不发信，可反复测）；"
    "``send`` 真发一封到 ``to``。参数名与「邮箱 SMTP 发送」凭据模板字段一一对齐"
    "（host / port / security / username / password / from_name），一条结构化凭据就能"
    "把这几个参数一次铺满；``mailkey`` 留给单值凭据验收。标成「来自凭据」后模型看不到"
    "这些值，由平台按 ``to`` 匹配后注入。返回每个参数的到达情况（非敏感给明文、敏感只给"
    "掩码与指纹）与真实连接结果（阶段 / 应答码 / 失败原因）。"
    "**只有用户明确要求「真发 / 真连测试」时才传 verify 或 send**。"
)
```

`send_mail_tool_schema()` 的 `properties` 末尾补：

```python
                "mode": {
                    "type": "string",
                    "enum": ["mock", "verify", "send"],
                    "default": "mock",
                    "description": (
                        "mock 只回显参数不发信；verify 真连并登录（不发信）；"
                        "send 真发一封到 to"
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "仅 send 使用：邮件正文，留空则用内置测试正文",
                },
```

并在该函数 docstring 里补一句：

```
    注意：模型实际看到的 schema 由 FastMCP 按 ``send_mail`` 的签名自动生成，
    本函数只是「服务连不上时管理页展示参数」的副本，签名一改必须同步，
    否则两份漂移（``mode`` / ``body`` 就是本次同步的对象）。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/services/mcp/test_echo_mail_real_mode.py -v`
Expected: PASS

- [ ] **Step 5: 向用户汇报，等待提交指示**

建议 commit（由用户执行）：

```bash
git add app/services/mcp/echo_server.py tests/services/mcp/test_echo_mail_real_mode.py
git commit -m "feat(echo): send_mail 补 mode/body 的工具描述与稳定 schema"
```

---

## Task 4: 兼容性回归（既有凭据注入用例）

**Files:**
- Modify: `tests/secrets/test_locator_e2e.py`（在该文件的 send_mail 区块追加）

- [ ] **Step 1: 追加兼容性断言**

在 `tests/secrets/test_locator_e2e.py` 的
`test_send_mail_tool_schema_seen_by_mcp_client_keeps_mailkey` 之后追加：

```python
async def test_send_mail_default_mode_stays_pure_mock():
    """mode 缺省必须是纯模拟：键集不变、不带 real，避免既有验收用例误判。"""
    from app.services.mcp.echo_server import send_mail

    out = await send_mail(None, "a@b.com", "hi", "smtp.qq.com", 465, "ssl",
                          "ops@b.com", "secret", "运维", "sk-alpha-111111")

    assert set(out) == {"ok", "to", "subject", "smtp", "mailkey", "message"}
    assert "real" not in out
    assert out["message"] == "已模拟给 a@b.com 发送邮件"
    assert out["smtp"]["password"]["fingerprint"]
    assert "secret" not in str(out["smtp"]["password"])
```

- [ ] **Step 2: 跑既有凭据注入测试确认不回归**

Run: `.venv/bin/python -m pytest tests/secrets/test_locator_e2e.py -v`
Expected: PASS（原有全部用例 + 新增 1 条）

- [ ] **Step 3: 跑更宽的凭据与 MCP 回归**

Run: `.venv/bin/python -m pytest tests/secrets tests/services/mcp tests/api/test_mcp_echo_server_api_contract.py tests/api/test_mcp_echo_platform_tool_selfheal.py -q`
Expected: 全部 PASS（无 failure；`no_infrastructure` 与连接库相关的既有 skip 属正常）

- [ ] **Step 4: 向用户汇报，等待提交指示**

建议 commit（由用户执行）：

```bash
git add tests/secrets/test_locator_e2e.py
git commit -m "test(echo): 锁定 send_mail 缺省仍为纯模拟"
```

---

## Task 5: 交付登记与设计文档回填

**Files:**
- Modify: `tests/CHECKLIST.md`（表格首行插入）
- Modify: `docs/superpowers/specs/2026-11-16-echo-send-mail-real-mode-design.md`（补 port/security 缺省约定）

- [ ] **Step 1: 补设计文档的实现期约定**

在 spec 的「参数校验分级」表格之后补一段：

```markdown
`port` 与 `security` 允许缺省：`port=None` 时按 `security` 推断（`ssl`→465，其余→587），
`security=None` 时复用 `EmailDeliveryService.infer_security` 按端口推断（465→ssl，
其余→starttls）。两者都缺省时按 `starttls` + 587，与个人中心「测试发信」的既有推断同源。
```

- [ ] **Step 2: 在 CHECKLIST 登记**

在 `tests/CHECKLIST.md` 的表格首行（最新在最上）之前插入一行，五列结构照抄现有行：

```markdown
| Echo send_mail 真实模式（Echo Mail Real Mode） | 后端：`app/services/mcp/echo_server.py`；测试：`tests/services/mcp/test_echo_mail_real_mode.py`（新增）、`tests/secrets/test_locator_e2e.py`；文档：`tests/CHECKLIST.md`、`docs/superpowers/specs/2026-11-16-echo-send-mail-real-mode-design.md` | **需求来源（用户）**：Echo 的 `send_mail` 是纯模拟回执，只能证明「注入的值到达了工具」，无法证明「值能用」——密码错、端口错、授权码过期时回执依然全绿。**做法**：同一工具、同一套凭据槽位（`arg:send_mail:*`）增加 `mode=mock/verify/send`（缺省 `mock`，返回体逐字段不变）：`verify` 用注入的 host/port/security/username/password 真连 SMTP 并登录（不发信，可反复测），`send` 真发一封到 `to`。**参数校验分级**：缺 host/username/password → `reason=missing_params`（指向「绑定没生效」）；port 越界 / security 非法 / 收件人非法 → `reason=invalid_params`（值到了但用法不对）；两者绝不降级成 mock。**出站守卫**：字面 IP 直判、域名走 `getaddrinfo` 全量解析，命中回环 / 私网 / 保留 / 组播 / 未指定 / 云元数据（含 `100.100.100.200`——Python 3.11 里它的 `is_private` 为 False，必须显式列网段）即 `reason=blocked_address` 并回显命中地址；**不复用** `url_policy._ip_is_blocked`，那里明确放行私网与 loopback。**阶段机**：connect → ehlo → starttls → login → sendmail，逐阶段记录 `stage`/`code`/`server`(截断 300)/`duration_ms`，失败不抛异常（否则拿不到回执）；`password` 全程过 `sanitize_error_text` 脱敏，证书校验不提供关闭入口。**取舍**：未防 DNS rebinding（解析后按 hostname 重连）；`mode` 由模型按意图传，安全性靠「默认 mock + 地址守卫」兜底；无认证 SMTP 不支持；内网 SMTP 会被守卫拦（需要内网联调得另加白名单）。**验证**：`tests/services/mcp/test_echo_mail_real_mode.py` 用进程内最小假 SMTP 服务器（不引 `aiosmtpd`，守卫用 monkeypatch 替身放行回环）覆盖 authenticated / 535 认证失败 / 550 收件人被拒 / 连接失败 / 密码不泄露；`tests/secrets/test_locator_e2e.py` 补「缺省仍为纯模拟」断言；`tests/secrets tests/services/mcp tests/api/test_mcp_echo_server_api_contract.py tests/api/test_mcp_echo_platform_tool_selfheal.py` 全绿。**未执行**：`./dev.sh`；真实 SMTP 凭据的实机验收（需用户在页面上配一条真实 QQ/企业邮箱凭据后点 `verify`） | ✅ 已实现；待用户实机验收 | 2026-11-16 |
```

- [ ] **Step 3: 向用户汇报，等待提交指示**

建议 commit（由用户执行）：

```bash
git add tests/CHECKLIST.md docs/superpowers/specs/2026-11-16-echo-send-mail-real-mode-design.md
git commit -m "docs(checklist): 登记 Echo send_mail 真实模式"
```

---

## 手工验收清单（需要用户在真实环境做）

这些必须用真实凭据，自动化测试替代不了：

- [ ] 在 MCP 管理页把一条真实邮箱凭据（QQ / 企业邮箱授权码）绑定到 `send_mail` 的 `host` / `port` / `security` / `username` / `password` 五个参数。
- [ ] 对话里让智能体调 `mode=verify`：成功时工具卡显示「已用注入参数登录成功（未发送邮件）」，`real.stage=authenticated`。
- [ ] 把凭据里的授权码改错一位再调 `verify`：应返回 `reason=auth_failed`，文案是「注入链路是通的……请检查凭据里的账号或授权码」，而不是「缺少参数」。
- [ ] 调 `mode=send` 到自己的邮箱：能收到主题为「注入验收」的邮件，正文含密码指纹。
- [ ] 把 `host` 临时填成 `127.0.0.1`：应被 `blocked_address` 拦下，工具卡给出「安全拦截，不是凭据问题」。
