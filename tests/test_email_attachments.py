"""send_email 附件：MIME 结构、路径校验、工具层契约。

背景：`send_email` 只能发正文，用户要报表/截图时只能自己下载再转发。
现在支持 `attachments`，但附件是「能把文件发出去」的能力，风险与纯文本完全不同，
所以这里钉住四件事：

1. **MIME 结构**：附件只影响最外层容器，正文的 multipart/alternative 语义与顺序不变；
   base64 往返无损，中文文件名不落成乱码。
2. **授权边界**：只能发当前会话已授权的附件或用户工作区里的文件，
   `/etc` 与临时目录里的文件一律拒绝——这是防「诱导模型外发服务器文件」的闸门。
3. **体量与类型**：白名单外的扩展名、>5 个、>20MB、合计 >20MB 全部在发信前拦下。
4. **失败即整封不发**：任何一个附件不合法都不会发出「少了几个附件」的邮件。
"""
from __future__ import annotations

import tempfile
from email import message_from_string
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.context import AgentContext, agent_context
from app.services import email_delivery_service as edmod
from app.services.ai.tools import document_paths as dp
from app.services.ai.tools.notification_tools import send_email
from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings
from app.services.notification_service import NotificationService

pytestmark = pytest.mark.no_infrastructure

MI = 1024 * 1024


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _settings() -> SmtpSettings:
    return SmtpSettings(
        host="smtp.example.com",
        port=587,
        user="sender@example.com",
        password="secret",
        security="starttls",
        from_address="sender@example.com",
        sender_name="AI 智能体平台",
    )


def _parse(message):
    return message_from_string(message.as_string())


def _decoded(part) -> str:
    return part.get_payload(decode=True).decode("utf-8")


def _build(body: str, *, attachments=None, render_markdown=False):
    kwargs = {"render_markdown": render_markdown}
    if attachments is not None:
        kwargs["attachments"] = attachments
    return EmailDeliveryService.build_message(
        settings=_settings(),
        to=["user@example.com"],
        subject="【AI 智能体平台】任务执行结果",
        body=body,
        **kwargs,
    )


def _attachment_parts(message):
    return [part for part in message.walk() if part.get_content_disposition() == "attachment"]


def _session_dir(root: Path, *, user_id: int = 1, conversation_id: str = "conv-1") -> Path:
    from app.services.ai.runtime.agentscope.workspace import resolve_session_workdir

    directory = Path(
        resolve_session_workdir(
            root=str(root), user_id=user_id, conversation_id=conversation_id
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _workspace_root(monkeypatch, root: Path) -> None:
    """把工作区根指到临时目录，其余路径函数都是纯函数，直接用真实实现。"""

    async def _root() -> str:
        return str(root)

    monkeypatch.setattr(dp, "resolve_workspace_root", _root)


def _write(directory: Path, name: str, data: bytes = b"hello") -> Path:
    path = directory / name
    path.write_bytes(data)
    return path


def _sparse(directory: Path, name: str, size: int) -> Path:
    """稀疏文件：瞬间造出大文件，不真占磁盘。"""
    path = directory / name
    with open(path, "wb") as handle:
        handle.seek(size - 1)
        handle.write(b"\0")
    return path


# --------------------------------------------------------------------------- #
# MIME 结构
# --------------------------------------------------------------------------- #
def test_no_attachments_keeps_existing_structure():
    """不传附件时必须是原来的单部分纯文本，不能因为新参数变成 multipart/mixed。"""
    message = _parse(_build("**不要渲染我**"))
    assert message.get_content_type() == "text/plain"
    assert _attachment_parts(message) == []


def test_attachments_wrap_alternative_without_reordering_body_parts():
    """根节点变 mixed，但正文的 alternative 与「纯文本在前」的顺序必须原样保留。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "report.pdf", b"%PDF-1.4 fake")
        message = _parse(_build("## 标题\n\n正文", attachments=[path], render_markdown=True))

        assert message.get_content_type() == "multipart/mixed"
        parts = message.get_payload()
        assert parts[0].get_content_type() == "multipart/alternative"
        assert [p.get_content_type() for p in parts[0].get_payload()] == [
            "text/plain",
            "text/html",
        ]
        assert "## 标题" in _decoded(parts[0].get_payload()[0])
        assert "<h2>标题</h2>" in _decoded(parts[0].get_payload()[1])

        attachments = _attachment_parts(message)
        assert len(attachments) == 1
        assert attachments[0].get_payload(decode=True) == b"%PDF-1.4 fake"


def test_plain_body_with_attachment_is_still_mixed():
    """人工文案（不渲染 Markdown）加附件时，正文是 text/plain，根节点仍是 mixed。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "data.csv", b"a,b\n1,2\n")
        message = _parse(_build("见附件", attachments=[path]))

        assert message.get_content_type() == "multipart/mixed"
        parts = message.get_payload()
        assert parts[0].get_content_type() == "text/plain"
        assert _decoded(parts[0]) == "见附件"
        assert _attachment_parts(message)[0].get_payload(decode=True) == b"a,b\n1,2\n"


def test_attachment_payload_roundtrips_and_chinese_filename_survives():
    payload = bytes(range(256)) * 4
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "季度报表.pdf", payload)
        message = _parse(_build("附件", attachments=[path]))

        attachment = _attachment_parts(message)[0]
        assert attachment.get_payload(decode=True) == payload
        assert attachment.get_filename() == "季度报表.pdf"
        assert attachment.get_content_type() == "application/pdf"


def test_unknown_extension_falls_back_to_octet_stream():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "data.bin", b"\x00\x01")
        message = _parse(_build("附件", attachments=[path]))

        assert _attachment_parts(message)[0].get_content_type() == "application/octet-stream"


def test_attachment_filename_control_characters_are_neutralized():
    """文件名里的换行不能漏进 MIME 头。

    标准库不会因此多造一个 Bcc 头（会做 RFC 2231 编码），但解析回来的文件名带着
    \\r\\n 会让部分客户端显示错乱，也容易被下游工具当成头分隔符误解。
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "a\r\nBcc: attacker@evil.com.pdf", b"data")
        message = _build("附件", attachments=[path])
        raw = message.as_string()

        assert not [line for line in raw.splitlines() if line.startswith("Bcc:")]
        name = _attachment_parts(_parse(message))[0].get_filename()
        assert "\r" not in name and "\n" not in name
        assert "attacker@evil.com" in name  # 只清洗控制字符，不丢内容


def test_attachment_size_cap_is_enforced_at_send_time():
    """校验与读取之间文件可能被替换：读取时按实际大小复核，超限则不发送。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _sparse(Path(tmp), "huge.pdf", dp.MAX_DOCUMENT_BYTES + 1)
        ok, message_text = EmailDeliveryService.send_mail(
            _settings(), ["user@example.com"], "标题", "正文", attachments=[path]
        )

        assert ok is False
        assert "20MB" in message_text


# --------------------------------------------------------------------------- #
# 路径校验
# --------------------------------------------------------------------------- #
async def test_resolve_keeps_order_and_dedupes(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    first = _write(directory, "a.txt", b"a")
    second = _write(directory, "b.pdf", b"b")

    resolved = await dp.resolve_email_attachments(
        [str(first), str(second), str(first)],
        allowed_attachment_paths=[],
        user_id=1,
        conversation_id="conv-1",
    )

    assert resolved == [first.resolve(), second.resolve()]


async def test_resolve_ignores_blank_entries(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    path = _write(directory, "a.txt", b"a")

    resolved = await dp.resolve_email_attachments(
        ["", "   ", str(path)],
        allowed_attachment_paths=[],
        user_id=1,
        conversation_id="conv-1",
    )

    assert resolved == [path.resolve()]


async def test_resolve_accepts_empty_input(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)

    resolved = await dp.resolve_email_attachments(
        [],
        allowed_attachment_paths=[],
        user_id=1,
        conversation_id="conv-1",
    )

    assert resolved == []


async def test_resolve_rejects_disallowed_extension(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    path = _write(directory, "deploy.sh", b"rm -rf /")

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            [str(path)],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert "不支持的文件类型" in str(excinfo.value)


async def test_resolve_rejects_file_outside_authorized_dirs(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    _session_dir(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            [str(outside)],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert "不在当前会话允许访问的目录中" in str(excinfo.value)


async def test_resolve_rejects_too_many_files(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    paths = [_write(directory, f"{index}.txt", b"x") for index in range(dp.MAX_EMAIL_ATTACHMENTS + 1)]

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            [str(p) for p in paths],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert str(dp.MAX_EMAIL_ATTACHMENTS) in str(excinfo.value)


async def test_resolve_rejects_oversized_single_file(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    path = _sparse(directory, "huge.pdf", dp.MAX_DOCUMENT_BYTES + 1)

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            [str(path)],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert "20MB" in str(excinfo.value)


async def test_resolve_rejects_oversized_total(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    each = dp.MAX_EMAIL_ATTACHMENT_TOTAL_BYTES // 2 + MI
    first = _sparse(directory, "a.pdf", each)
    second = _sparse(directory, "b.pdf", each)

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            [str(first), str(second)],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert "合计" in str(excinfo.value)


def test_email_attachment_error_is_a_document_path_error():
    """工具层统一按 DocumentPathError 捕获，子类关系不能断。"""
    assert issubclass(dp.EmailAttachmentError, dp.DocumentPathError)


def test_attachment_size_caps_stay_in_sync():
    """工具层放过的文件不能到了发送阶段才失败：两处 20MB 口径必须一致（单文件与合计）。"""
    assert edmod.MAX_ATTACHMENT_BYTES == dp.MAX_DOCUMENT_BYTES
    assert edmod.MAX_ATTACHMENT_TOTAL_BYTES == dp.MAX_EMAIL_ATTACHMENT_TOTAL_BYTES


def test_migration_updates_tool_description_in_both_databases():
    """只改 MySQL 不改 PostgreSQL，会让 PG 部署的工具面板继续显示旧文案。"""
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "db-prod/V182-update_email_tool_description_attachments.sql",
        "db-prod-pg/V83-update_email_tool_description_attachments.sql",
    ):
        sql = (root / relative).read_text(encoding="utf-8")
        assert "name = 'send_email'" in sql, f"{relative} 没更新到 send_email"
        assert "附件" in sql, f"{relative} 的描述没提到附件能力"


# --------------------------------------------------------------------------- #
# 工具层
# --------------------------------------------------------------------------- #
class _RecordingMailer:
    def __init__(self, *, ok: bool = True, message: str = "发送成功") -> None:
        self.calls: list[dict] = []
        self._ok = ok
        self._message = message

    def __call__(self, settings, to, subject, body, **kwargs):
        self.calls.append(
            {"settings": settings, "to": to, "subject": subject, "body": body, **kwargs}
        )
        return self._ok, self._message


def _install_tool_env(monkeypatch, *, config: dict, mailer: _RecordingMailer):
    async def _fake_config(cls, db, user_id, channel_type):
        import json

        return SimpleNamespace(config_json=json.dumps(config))

    async def _fake_settings(cls, user_id, scope="user", db=None):
        return _settings()

    class _NullSession:
        """工具会 `async with AsyncSessionLocal()`；校验逻辑已被 monkeypatch，不需要真库。"""

        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    import app.core.orm as orm_module

    monkeypatch.setattr(orm_module, "AsyncSessionLocal", _NullSession)
    monkeypatch.setattr(
        NotificationService, "get_config_by_type_raw", classmethod(_fake_config)
    )
    monkeypatch.setattr(
        EmailDeliveryService, "resolve_smtp_settings", classmethod(_fake_settings)
    )
    monkeypatch.setattr(EmailDeliveryService, "send_mail", staticmethod(mailer))


async def _call_tool(**kwargs):
    return await send_email()._arun(**kwargs)


async def test_tool_sends_attachments_with_workspace_paths(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    report = _write(directory, "report.xlsx", b"xlsx-bytes")
    mailer = _RecordingMailer()
    _install_tool_env(
        monkeypatch,
        config={"is_enabled": True, "include_self": True, "recipients": "boss@example.com"},
        mailer=mailer,
    )

    token = agent_context.set(
        AgentContext(
            agent_id="agent-1",
            agent_name="报表助手",
            user_id=1,
            conversation_id="conv-1",
        )
    )
    try:
        result = await _call_tool(
            to_email="", subject="周报", content="见附件", attachments=[str(report)]
        )
    finally:
        agent_context.reset(token)

    assert result.startswith("Successfully sent email")
    assert "1 attachment" in result
    assert len(mailer.calls) == 1
    call = mailer.calls[0]
    assert call["to"] == ["boss@example.com"]
    assert call["attachments"] == [report.resolve()]
    assert call["render_markdown"] is True


async def test_tool_accepts_single_string_attachment(monkeypatch, tmp_path):
    """模型偶尔把数组写成单个字符串，不能因此整封失败。"""
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    report = _write(directory, "report.pdf", b"pdf")
    mailer = _RecordingMailer()
    _install_tool_env(
        monkeypatch,
        config={"is_enabled": True, "include_self": True, "recipients": "boss@example.com"},
        mailer=mailer,
    )

    token = agent_context.set(
        AgentContext(agent_id="agent-1", agent_name="报表助手", user_id=1, conversation_id="conv-1")
    )
    try:
        result = await _call_tool(
            to_email="", subject="周报", content="见附件", attachments=str(report)
        )
    finally:
        agent_context.reset(token)

    assert result.startswith("Successfully sent email")
    assert mailer.calls[0]["attachments"] == [report.resolve()]


async def test_tool_does_not_send_when_attachment_invalid(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    _session_dir(tmp_path)
    mailer = _RecordingMailer()
    _install_tool_env(
        monkeypatch,
        config={"is_enabled": True, "include_self": True, "recipients": "boss@example.com"},
        mailer=mailer,
    )

    token = agent_context.set(
        AgentContext(agent_id="agent-1", agent_name="报表助手", user_id=1, conversation_id="conv-1")
    )
    try:
        result = await _call_tool(
            to_email="",
            subject="周报",
            content="见附件",
            attachments=[str(tmp_path / "missing.pdf")],
        )
    finally:
        agent_context.reset(token)

    assert result.startswith("Error:")
    assert "missing.pdf" in result
    assert mailer.calls == [], "附件不合法时绝不能把缺附件的邮件发出去"


async def test_tool_without_attachments_unchanged(monkeypatch, tmp_path):
    """回归：不传附件时调用面与以前完全一致（不出现 attachments=[] 之外的变化）。"""
    _workspace_root(monkeypatch, tmp_path)
    mailer = _RecordingMailer()
    _install_tool_env(
        monkeypatch,
        config={"is_enabled": True, "include_self": True, "recipients": "boss@example.com"},
        mailer=mailer,
    )

    token = agent_context.set(
        AgentContext(agent_id="agent-1", agent_name="报表助手", user_id=1, conversation_id="conv-1")
    )
    try:
        result = await _call_tool(to_email="", subject="周报", content="正文")
    finally:
        agent_context.reset(token)

    assert result.startswith("Successfully sent email")
    assert "attachment" not in result
    assert mailer.calls[0]["attachments"] == []
    assert mailer.calls[0]["render_markdown"] is True


# --------------------------------------------------------------------------- #
# 评审补充：兼容性、边界与安全
# --------------------------------------------------------------------------- #
def _ssl_settings() -> SmtpSettings:
    return SmtpSettings(
        host="smtp.example.com",
        port=465,
        user="sender@example.com",
        password="secret",
        security="ssl",
        from_address="sender@example.com",
        sender_name="AI 智能体平台",
    )


def test_ascii_filename_uses_plain_filename_parameter():
    """ASCII 名不该被写成 filename*=utf-8''report.pdf：老 Outlook 与企业网关会显示成
    百分号串。非 ASCII 才需要 RFC 2231。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "report.pdf", b"%PDF")
        raw = _build("附件", attachments=[path]).as_string()

        assert 'filename="report.pdf"' in raw
        assert "filename*=" not in raw


def test_chinese_filename_still_uses_rfc2231():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "季度报表.pdf", b"%PDF")
        message = _parse(_build("附件", attachments=[path]))
        disposition = str(_attachment_parts(message)[0]["Content-Disposition"])

        assert "filename*=" in disposition
        assert "=?utf-8?" not in disposition  # 不用 RFC2047 塞参数，客户端解析更稳


def test_date_and_message_id_headers_present():
    """带附件的邮件更容易被判垃圾：补齐 Date / Message-ID 是成本最低的改善。"""
    message = _build("正文")
    assert message["Date"]
    assert str(message["Message-ID"]).startswith("<") and "@" in str(message["Message-ID"])


def test_attachment_total_cap_enforced_in_build_message():
    """工具层之外直接调 build_message/send_mail 也要受限，否则 5×20MB 能绕过合计上限。"""
    each = edmod.MAX_ATTACHMENT_TOTAL_BYTES // 2 + MI
    with tempfile.TemporaryDirectory() as tmp:
        first = _sparse(Path(tmp), "a.pdf", each)
        second = _sparse(Path(tmp), "b.pdf", each)
        with pytest.raises(edmod.AttachmentPreparationError) as excinfo:
            _build("附件", attachments=[first, second])

    assert "合计" in str(excinfo.value)


def test_missing_file_at_send_time_returns_error_without_path(monkeypatch):
    """校验后文件被删：返回业务错误，不抛异常，也不把容器绝对路径透给用户。"""
    ok, message_text = EmailDeliveryService.send_mail(
        _settings(),
        ["user@example.com"],
        "标题",
        "正文",
        attachments=[Path("/nonexistent/secret-dir/report.pdf")],
    )

    assert ok is False
    assert "report.pdf" in message_text
    assert "/nonexistent/secret-dir" not in message_text


def test_attachment_send_uses_longer_timeout(monkeypatch):
    """10 秒的 socket timeout 撑不住 27MB base64：有附件时必须放大。"""
    seen: dict = {}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            seen["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, payload):
            seen["sent"] = len(payload)

    monkeypatch.setattr(edmod.smtplib, "SMTP_SSL", _FakeSMTP)

    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "a.pdf", b"x" * 1024)
        ok, _ = EmailDeliveryService.send_mail(
            _ssl_settings(), ["user@example.com"], "标题", "正文", attachments=[path]
        )

    assert ok is True and seen["sent"] > 0
    assert seen["timeout"] >= 60


def test_markdown_extension_maps_to_text_markdown():
    """slim 镜像里没有 /etc/mime.types，.md 会退成 octet-stream，客户端无法预览。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write(Path(tmp), "notes.md", b"# hi")
        part = _attachment_parts(_parse(_build("附件", attachments=[path])))[0]

        assert part.get_content_type() == "text/markdown"


async def test_resolve_rejects_symlink_escape(monkeypatch, tmp_path):
    """符号链接不能成为逃逸通道：resolve() 之后必须仍落在授权目录内。"""
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    secret = tmp_path.parent / "outside-secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = directory / "notes.txt"
    link.symlink_to(secret)

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            [str(link)],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert "不在当前会话允许访问的目录中" in str(excinfo.value)


async def test_resolve_rejects_parent_directory_traversal(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(dp.EmailAttachmentError):
        await dp.resolve_email_attachments(
            [str(directory / ".." / ".." / ".." / "outside.txt")],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )


async def test_resolve_accepts_uppercase_extension(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    path = _write(directory, "REPORT.PDF", b"%PDF")

    resolved = await dp.resolve_email_attachments(
        [str(path)],
        allowed_attachment_paths=[],
        user_id=1,
        conversation_id="conv-1",
    )

    assert resolved == [path.resolve()]


async def test_resolve_rejects_double_extension(monkeypatch, tmp_path):
    """x.pdf.sh 的真实后缀是 .sh，不能因为名字里含 .pdf 就放过。"""
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    path = _write(directory, "x.pdf.sh", b"rm -rf /")

    with pytest.raises(dp.EmailAttachmentError):
        await dp.resolve_email_attachments(
            [str(path)],
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )


async def test_resolve_accepts_exactly_max_count(monkeypatch, tmp_path):
    _workspace_root(monkeypatch, tmp_path)
    directory = _session_dir(tmp_path)
    paths = [_write(directory, f"{index}.txt", b"x") for index in range(dp.MAX_EMAIL_ATTACHMENTS)]

    resolved = await dp.resolve_email_attachments(
        [str(p) for p in paths],
        allowed_attachment_paths=[],
        user_id=1,
        conversation_id="conv-1",
    )

    assert len(resolved) == dp.MAX_EMAIL_ATTACHMENTS


async def test_resolve_rejects_too_many_before_touching_disk(monkeypatch, tmp_path):
    """100 个路径不该先做 100 次授权解析（每次都读工作区配置）才报数量错。"""
    _workspace_root(monkeypatch, tmp_path)
    _session_dir(tmp_path)
    missing = [str(tmp_path / f"nope-{index}.pdf") for index in range(100)]

    with pytest.raises(dp.EmailAttachmentError) as excinfo:
        await dp.resolve_email_attachments(
            missing,
            allowed_attachment_paths=[],
            user_id=1,
            conversation_id="conv-1",
        )

    assert f"最多 {dp.MAX_EMAIL_ATTACHMENTS} 个" in str(excinfo.value)


def test_only_tool_passes_attachments_to_send_mail():
    """「只有 send_email 工具能带附件」是明确决策：定时通知/审核/密码重置都不带。

    这条用 AST 扫全仓，防止后人顺手给别的发信路径加上附件。
    """
    import ast

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for source in (root / "app").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            # 两种形态：直接调用，以及作为 to_thread 的第一个可调用参数传入
            threaded = bool(node.args) and (
                getattr(node.args[0], "attr", None) == "send_mail"
            )
            if name != "send_mail" and not threaded:
                continue
            if any(keyword.arg == "attachments" for keyword in node.keywords):
                offenders.append(str(source.relative_to(root)))

    assert offenders == ["app/services/ai/tools/notification_tools.py"]
