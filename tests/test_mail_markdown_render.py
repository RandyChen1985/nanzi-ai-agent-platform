"""邮件正文的 Rich Text 渲染：行为 + 调用面契约。

背景：`send_mail` 长期用 `MIMEText(body, "plain")` 单部分发送，而工具 send_email 与
定时通知的正文都是模型产出的 Markdown。结果钉钉/企微/飞书（msgtype=markdown）渲染得
漂漂亮亮，邮件里用户看到的是满屏 `**`、`##` 和 `|`——同一条通知换个渠道就换一副面孔。

现在改成 multipart/alternative：纯文本原文兜底 + 渲染后的 HTML。这里钉住三件事：

1. **渲染结果**：GFM 表格、段内换行、样式内联都要真的生效（表格没边框等于没渲染）；
2. **安全**：正文来自模型，最终进的是用户邮箱。原始 HTML 必须被转义，
   `javascript:` 之类的协议必须被拒——这条不能被任何"渲染更丰富"的改动牺牲；
3. **调用面**：只有「模型产出正文」的两条路径开渲染。人工编写的文案
   （审核、密码重置、测试邮件）里成对的 `*`/`_` 会被 Markdown 误解析成斜体粗体，
   必须留在纯文本，且默认参数就是关的。
"""
import ast
import inspect
from email import message_from_string
from email.header import decode_header, make_header
from pathlib import Path

import pytest

from app.services import email_delivery_service as edmod
from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[1]


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
    """把构造好的报文转回 MIME 树，避免直接对字符串做脆弱的文本断言。"""
    return message_from_string(message.as_string())


def _decoded(part) -> str:
    return part.get_payload(decode=True).decode("utf-8")


_UNSET = object()


def _build(body: str, *, render_markdown=_UNSET):
    """render_markdown 不传时走函数默认值——「默认必须是关的」本身就是要测的行为。"""
    kwargs = {} if render_markdown is _UNSET else {"render_markdown": render_markdown}
    return EmailDeliveryService.build_message(
        settings=_settings(),
        to=["user@example.com"],
        subject="【AI 智能体平台】任务执行结果",
        body=body,
        **kwargs,
    )


def _render(body: str) -> str:
    return edmod.render_markdown_to_html(body)


# --------------------------------------------------------------------------- #
# 渲染行为
# --------------------------------------------------------------------------- #
def test_headings_lists_and_emphasis_are_rendered():
    html = _render("## 核心结论\n\n- 增长 12%\n- **重点**：留存下降")
    assert "<h2>核心结论</h2>" in html
    assert "<li>增长 12%</li>" in html
    assert "<strong>重点</strong>" in html


def test_gfm_table_is_rendered_with_inline_styles():
    """表格必须渲染成 <table> 且**带边框**：邮件客户端会剥掉 <style>，只能内联。"""
    html = _render("| 指标 | 值 |\n| --- | --- |\n| GMV | 123 |")
    assert "<table" in html and "border-collapse:collapse" in html
    assert "<th style=" in html and "<td style=" in html
    # <thead>/<tbody> 不能被 th/td 规则误伤（否则结构标签会被插进 style 而损坏）
    assert "<thead>" in html and "<tbody>" in html


def test_single_newline_becomes_br():
    """一行一个字段的排版（账号名：… / 邮箱：…）不能被并成一行。"""
    html = _render("账号名：张三\n邮箱：a@b.com")
    assert "<br" in html
    assert "账号名：张三" in html and "邮箱：a@b.com" in html


def test_code_block_keeps_language_class_and_gains_style():
    html = _render("```sql\nSELECT 1;\n```")
    assert "<pre style=" in html
    assert "<code style=" in html and 'class="language-sql"' in html


def test_chinese_and_emoji_survive():
    html = _render("完成 ✅ 你好，世界 🌏")
    assert "完成 ✅ 你好，世界 🌏" in html


def test_empty_body_does_not_explode():
    html = _render("")
    assert html.startswith("<div style=")


# --------------------------------------------------------------------------- #
# 安全：正文来自模型，最终进用户邮箱
# --------------------------------------------------------------------------- #
def test_raw_html_is_escaped():
    html = _render("<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>")
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_dangerous_link_protocol_is_not_turned_into_anchor():
    html = _render("[点我](javascript:alert(1))")
    assert "<a " not in html and "href" not in html


def test_normal_link_renders_with_style():
    html = _render("见 [报表](https://example.com/x)")
    assert 'href="https://example.com/x"' in html
    assert "<a style=" in html


# --------------------------------------------------------------------------- #
# MIME 结构
# --------------------------------------------------------------------------- #
def test_render_markdown_produces_alternative_with_plain_fallback_first():
    message = _parse(_build("## 标题\n\n**增长** 12%", render_markdown=True))

    assert message.get_content_type() == "multipart/alternative"
    parts = message.get_payload()
    # 顺序不能反：RFC 2046 让客户端取「最后一个能显示的版本」，
    # HTML 放前面会让纯文本客户端什么都看不到。
    assert [part.get_content_type() for part in parts] == ["text/plain", "text/html"]

    # 纯文本版是 Markdown **原文**：HTML 被过滤时仍能读，不丢信息
    assert "## 标题" in _decoded(parts[0])
    assert "<h2>标题</h2>" in _decoded(parts[1])


def test_headers_survive_multipart_encoding():
    message = _parse(_build("正文", render_markdown=True))
    assert str(make_header(decode_header(message["Subject"]))) == "【AI 智能体平台】任务执行结果"
    assert "user@example.com" in str(message["To"])
    assert "smtp" not in str(message["From"])  # 只暴露发件地址，绝不泄露凭据
    assert "sender@example.com" in str(make_header(decode_header(message["From"])))


def test_plain_text_single_part_by_default():
    """**不传参**时必须是纯文本单部分。

    既有的 6 条人工文案发信路径全都不传这个参数，默认值一旦翻成 True，
    审核通知、密码重置邮件就会一起被 Markdown 渲染——这条守住那个默认值。
    """
    message = _parse(_build("**不要渲染我**"))
    assert message.get_content_type() == "text/plain"
    assert "**不要渲染我**" in _decoded(message)


def test_render_markdown_defaults_to_off():
    """默认值直接钉在函数签名上：人工文案路径不该依赖调用方记得传 False。"""
    for func in (EmailDeliveryService.send_mail, EmailDeliveryService.build_message):
        default = inspect.signature(func).parameters["render_markdown"].default
        assert default is False, f"{func.__name__} 的 render_markdown 默认值必须为 False"


def test_render_failure_degrades_to_plain_text(monkeypatch):
    """渲染出问题不能把通知本身弄丢：退回纯文本照发。"""

    def _boom(_body: str) -> str:
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(edmod, "render_markdown_to_html", _boom)
    message = _parse(_build("**正文**", render_markdown=True))
    assert message.get_content_type() == "text/plain"
    assert "**正文**" in _decoded(message)


# --------------------------------------------------------------------------- #
# 调用面契约：谁能开渲染
# --------------------------------------------------------------------------- #
def _send_mail_calls(relative_path: str) -> list:
    """收集文件里所有「发信调用」节点。

    两种形态都要覆盖：
    - 直接调用 `EmailDeliveryService.send_mail(...)`；
    - 作为线程池的第一个可调用参数传入（`to_thread(send_mail, ...)`），
      此时关键字参数挂在外层调用上——正是 `_arun` 里那个写法。
    """
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "send_mail":
            calls.append(node)
        elif node.args and isinstance(node.args[0], ast.Attribute):
            if node.args[0].attr == "send_mail":
                calls.append(node)
    return calls


def _flag(node, name: str):
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value.value if isinstance(keyword.value, ast.Constant) else "non-literal"
    return None


def test_tool_email_enables_markdown_rendering():
    calls = _send_mail_calls("app/services/ai/tools/notification_tools.py")
    assert len(calls) == 1, "send_email 工具的发信调用数量变了，契约需同步复核"
    assert _flag(calls[0], "render_markdown") is True


def test_scheduled_email_enables_markdown_but_connectivity_test_does_not():
    """同一个文件里两条路径要分开看：定时通知开渲染，连通性测试邮件保持纯文本。"""
    calls = _send_mail_calls("app/services/notification_service.py")
    assert len(calls) == 2, "notification_service 的发信调用数量变了，契约需同步复核"

    # 需要退避重试的那条就是定时通知投递（唯一带 raise_on_transport_error 的）
    scheduled = [call for call in calls if _flag(call, "raise_on_transport_error") is True]
    assert len(scheduled) == 1
    assert _flag(scheduled[0], "render_markdown") is True

    connectivity = [call for call in calls if call not in scheduled]
    assert _flag(connectivity[0], "render_markdown") is None


@pytest.mark.parametrize(
    "relative_path",
    [
        "app/api/portal/endpoints/auth.py",
        "app/api/portal/endpoints/system.py",
        "app/api/portal/endpoints/management.py",
        "app/services/password_reset_service.py",
        "app/services/email_delivery_service.py",
    ],
)
def test_human_written_mail_stays_plain_text(relative_path: str):
    """人工文案不开渲染：成对的 `*`/`_` 会被 Markdown 误解析，属真实回归风险。"""
    calls = _send_mail_calls(relative_path)
    assert calls, f"{relative_path} 里没找到发信调用，这条契约会空跑"
    for call in calls:
        assert _flag(call, "render_markdown") is None, (
            f"{relative_path} 的人工文案不该开 Markdown 渲染"
        )
