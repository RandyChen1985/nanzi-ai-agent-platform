"""Echo send_mail 真实模式：参数校验与出站地址守卫。"""

from __future__ import annotations

import json
import socket
import threading

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

    @pytest.mark.parametrize("port", [True, False])
    def test_boolean_port_is_rejected_not_coerced(self, port):
        """`int(True) == 1` 会顺着 1..65535 的范围校验溜过去。

        凭据里 JSON 写成 `"port": true` 时，最终表现成「连接端口 1 失败」——
        把一个类型错误伪装成网络错误，排查方向全错。
        """
        result = echo_server._validate_real_mail_params(
            to="a@b.com", host="smtp.qq.com", port=port, security="ssl",
            username="ops@b.com", password="secret",
        )
        assert result["ok"] is False
        assert result["reason"] == "invalid_params"

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
         "100.100.100.200", "0.0.0.0", "::1", "fd00::1", "[::1]",
         # IPv4-mapped IPv6：3.11 里 is_private / is_reserved 全是 False，且
         # version == 6 会跳过 IPv4 网段表——一条 AAAA 记录就能把元数据送进来
         "::ffff:100.100.100.200", "::ffff:168.63.129.16", "::ffff:127.0.0.1"],
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
        def boom(host, port):
            raise socket.gaierror("Name or service not known")

        monkeypatch.setattr(echo_server, "_resolve_host_addresses", boom)
        result = await echo_server._guard_smtp_host("no-such-host.invalid", 25)
        assert result["ok"] is False
        assert result["reason"] == "dns_failed"

    async def test_rejects_control_chars_and_empty(self):
        assert (await echo_server._guard_smtp_host("", 25))["reason"] == "invalid_params"
        assert (
            await echo_server._guard_smtp_host("smtp\r\nqq.com", 25)
        )["reason"] == "invalid_params"
        assert (await echo_server._guard_smtp_host("x" * 300, 25))["reason"] == "invalid_params"


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
                    send(
                        "235 2.7.0 Authentication successful"
                        if self.auth_ok
                        else "535 5.7.8 Authentication credentials invalid"
                    )
                elif upper.startswith("MAIL FROM"):
                    send("250 2.1.0 Ok")
                elif upper.startswith("RCPT TO"):
                    send(
                        "550 5.1.1 Recipient address rejected"
                        if self.reject_recipient
                        else "250 2.1.5 Ok"
                    )
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
        # 535 的几种原因在回执上长得一样，用户没法自己区分，所以文案要列出来。
        # 「没开 SMTP 服务」和「填了登录密码而不是授权码」占了绝大多数。
        for hint in ("POP3/SMTP 服务", "授权码", "空格", "完整邮箱地址"):
            assert hint in result["message"], hint

    def test_auth_failure_wording_adapts_to_mode(self):
        """verify 是「校验失败」，send 是「发送失败」，别混着说。"""
        with FakeSmtpServer(auth_ok=False) as server:
            verified = self._run(port=server.port, mode="verify")
        with FakeSmtpServer(auth_ok=False) as server:
            sent = self._run(port=server.port, mode="send")
        assert verified["message"].startswith("真实校验失败")
        assert sent["message"].startswith("真实发送失败")

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

    async def test_blocked_address_reports_hits(self):
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
        description = echo_server.ECHO_MAIL_TOOL_DESCRIPTION
        assert "mock" in description
        assert "verify" in description
        assert "send" in description
