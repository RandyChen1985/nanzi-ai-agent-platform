"""分享服务的纯函数契约：密码字符集、有效期换算、令牌时效、正文清洗。"""

from datetime import datetime, timedelta

import pytest

from app.services import conversation_share_service as svc

pytestmark = pytest.mark.no_infrastructure

NOW = datetime(2026, 10, 28, 13, 20, 0)


def test_generated_secret_avoids_confusable_characters():
    secrets_seen = {svc.generate_secret() for _ in range(50)}
    assert len(secrets_seen) > 40, "每次都相同说明没用系统随机源"
    for value in secrets_seen:
        assert len(value) == svc.SECRET_LENGTH
        assert not set(value) & set("0O1lI"), value
        assert all(ch.isalnum() for ch in value)


def test_custom_secret_length_is_validated():
    assert svc.normalize_secret("  abcd1234  ") == "abcd1234"
    with pytest.raises(ValueError):
        svc.normalize_secret("abc")
    with pytest.raises(ValueError):
        svc.normalize_secret("x" * 33)
    with pytest.raises(ValueError):
        svc.normalize_secret("   ")


@pytest.mark.parametrize(
    "preset,expected_delta",
    [("1h", timedelta(hours=1)), ("24h", timedelta(hours=24)),
     ("7d", timedelta(days=7)), ("30d", timedelta(days=30))],
)
def test_preset_expiry(preset, expected_delta):
    assert svc.resolve_expires_at({"mode": "preset", "preset": preset}, now=NOW) == NOW + expected_delta


def test_permanent_and_custom_expiry():
    assert svc.resolve_expires_at({"mode": "permanent"}, now=NOW) is None
    assert svc.resolve_expires_at(
        {"mode": "custom", "value": 3, "unit": "day"}, now=NOW
    ) == NOW + timedelta(days=3)
    assert svc.resolve_expires_at(
        {"mode": "custom", "value": 6, "unit": "hour"}, now=NOW
    ) == NOW + timedelta(hours=6)
    with pytest.raises(ValueError):
        svc.resolve_expires_at({"mode": "custom", "value": 0, "unit": "day"}, now=NOW)
    with pytest.raises(ValueError):
        svc.resolve_expires_at({"mode": "custom", "value": 999, "unit": "day"}, now=NOW)
    with pytest.raises(ValueError):
        svc.resolve_expires_at({"mode": "custom", "value": 1, "unit": "week"}, now=NOW)
    with pytest.raises(ValueError):
        svc.resolve_expires_at({"mode": "preset", "preset": "2y"}, now=NOW)


def test_access_token_roundtrip_and_expiry():
    token, expires_in = svc.issue_access_token("share-abc", NOW + timedelta(days=7), now=NOW)
    assert svc.verify_access_token(token, "share-abc", now=NOW + timedelta(hours=1)) is True
    # 跨分享复用必须失败
    assert svc.verify_access_token(token, "share-other", now=NOW) is False
    # 超过 12 小时上限即失效（即使分享本身还有效）
    assert svc.verify_access_token(token, "share-abc", now=NOW + timedelta(hours=13)) is False
    assert expires_in == int(timedelta(hours=12).total_seconds())


def test_token_cannot_outlive_share_expiry():
    token, expires_in = svc.issue_access_token("share-abc", NOW + timedelta(hours=2), now=NOW)
    assert expires_in == int(timedelta(hours=2).total_seconds())
    assert svc.verify_access_token(token, "share-abc", now=NOW + timedelta(hours=3)) is False


def test_access_token_rejects_garbage():
    assert svc.verify_access_token("not-a-token", "share-abc", now=NOW) is False
    assert svc.verify_access_token("", "share-abc", now=NOW) is False


def test_secret_roundtrip_and_tamper():
    cipher = svc.encrypt_secret("abcd1234")
    assert cipher != "abcd1234"
    assert svc.decrypt_secret(cipher) == "abcd1234"
    assert svc.decrypt_secret("not-a-valid-cipher") is None


def test_secret_cipher_fits_column():
    """Fernet token 再 base64 一层约 188 字符，必须落在 VARCHAR(512) 内（不截断）。"""
    cipher = svc.encrypt_secret("abcd1234")
    assert svc.decrypt_secret(cipher) == "abcd1234"
    assert len(cipher) < 512


def test_verify_secret():
    cipher = svc.encrypt_secret("abcd1234")
    assert svc.verify_secret(cipher, "abcd1234") is True
    assert svc.verify_secret(cipher, "abcd1235") is False
    assert svc.verify_secret("not-a-valid-cipher", "abcd1234") is False


def test_verify_secret_supports_non_ascii_custom_secret():
    """自定义密码允许中文；compare_digest 直接比 str 会因非 ASCII 抛 TypeError。"""
    cipher = svc.encrypt_secret("数据分析密码")
    assert svc.verify_secret(cipher, "数据分析密码") is True
    assert svc.verify_secret(cipher, "数据分析密玛") is False
    assert svc.normalize_secret("  数据分析密码  ") == "数据分析密码"


def test_sanitize_removes_internal_reasoning_and_sql():
    raw = (
        "结论如下。\n"
        "<thought>内部想了很久</thought>\n"
        ":::analysis 分析\n这里是推理\n:::\n"
        "<sql_plan>{\"sql\":\"select * from secret_table\"}</sql_plan>\n"
        "| 月份 | GMV |\n| --- | --- |\n| 09 | 463.9 |\n"
    )
    cleaned = svc.sanitize_assistant_content(raw)
    assert "内部想了很久" not in cleaned
    assert "这里是推理" not in cleaned
    assert "secret_table" not in cleaned
    assert "463.9" in cleaned, "正文表格不能一起被吃掉"


@pytest.mark.parametrize(
    "raw",
    [
        # 相对路径（前端拼 API base）
        "已生成文件：[分析结果.xlsx](/api/v1/chat/generated-files/" + "a" * 32 + "?token=xyz)",
        # 绝对路径
        "已生成文件：[分析结果.xlsx](https://example.com/api/v1/chat/generated-files/"
        + "a" * 32
        + "?token=xyz)",
    ],
)
def test_sanitize_downgrades_generated_file_links(raw):
    cleaned = svc.sanitize_assistant_content(raw)
    assert "generated-files" not in cleaned
    assert "分析结果.xlsx" in cleaned
    assert "文件未随分享提供" in cleaned


def test_sanitize_downgrades_bare_generated_url():
    cleaned = svc.sanitize_assistant_content(
        "下载：https://example.com/api/v1/chat/generated-files/" + "b" * 32 + "?token=xyz"
    )
    assert "generated-files" not in cleaned
    assert "文件未随分享提供" in cleaned


def test_sanitize_strips_quick_buttons():
    raw = "请选择：[继续分析](quick:%E7%BB%A7%E7%BB%AD) 或 [查看明细](quick:detail)"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "quick:" not in cleaned
    assert "继续分析" not in cleaned
    assert "查看明细" not in cleaned


def test_sanitize_strips_quick_button_blocks_without_dangling_bullets():
    """真实输出形态：引导语 + 「- [🙋 标签](quick:…)」列表，摘掉后不能剩空项目符号。"""
    raw = (
        "结论如下：\n"
        "您可以这样继续：\n"
        "- [🙋 看下 9 月数据](quick:看下9月数据)\n"
        "- [🙋 换成 GMV](quick:换成GMV)\n"
    )
    cleaned = svc.sanitize_assistant_content(raw)
    assert "quick:" not in cleaned
    assert "您可以这样继续" not in cleaned
    assert "看下 9 月数据" not in cleaned
    assert "- " not in cleaned
    assert cleaned.strip() == "结论如下："


def test_sanitize_strips_bold_quick_intro_and_trailing_separator():
    """加粗引导语 + 前面的 ``---`` 分隔线也要一并收掉。"""
    raw = "结论如下：\n\n---\n\n**您还可以继续：**\n- [🙋 换维度](quick:换维度)\n"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "您还可以继续" not in cleaned
    assert "quick:" not in cleaned
    assert "---" not in cleaned
    assert cleaned.strip() == "结论如下："


def test_sanitize_strips_quick_html_buttons():
    raw = '看这个：<a class="quick-action-btn" href="quick:detail">查看明细</a>'
    cleaned = svc.sanitize_assistant_content(raw)
    assert "quick:" not in cleaned
    assert "查看明细" not in cleaned
    assert "看这个" in cleaned


def test_sanitize_keeps_prose_around_inline_quick_link():
    raw = "先看这个结论：[展开说明](quick:展开说明)，其余我们下轮再说。"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "quick:" not in cleaned
    assert "先看这个结论" in cleaned
    assert "其余我们下轮再说" in cleaned


def test_sanitize_drops_unclosed_reasoning_blocks():
    """没闭合的推理块：后面整段都不该出现在公开页。"""
    cleaned = svc.sanitize_assistant_content("结论在这里。\n<thought>还没写完的内部推理")
    assert "结论在这里。" in cleaned
    assert "内部推理" not in cleaned

    cleaned = svc.sanitize_assistant_content("结论在这里。\n:::analysis 分析\n未完的推理")
    assert "结论在这里。" in cleaned
    assert "未完的推理" not in cleaned


def test_sanitize_keeps_plain_markdown():
    raw = "## 小结\n\n- 是的\n- **重点**\n\n[官网](https://example.com)"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "## 小结" in cleaned
    assert "**重点**" in cleaned
    assert "[官网](https://example.com)" in cleaned


def test_sanitize_keeps_chart_and_code_blocks():
    """图表 / mermaid / chart 标记属于正文，清洗不能顺手吃掉。"""
    raw = (
        '```echarts\n{"series": [1, 2]}\n```\n\n'
        "```mermaid\ngraph LR\n  A-->B\n```\n\n"
        '<chart type="bar">{"k": 1}</chart>'
    )
    cleaned = svc.sanitize_assistant_content(raw)
    assert "```echarts" in cleaned
    assert "```mermaid" in cleaned
    assert "<chart" in cleaned


def test_sanitize_collapses_blank_lines_left_by_removed_blocks():
    raw = "第一段。\n\n<thought>推理</thought>\n\n<sql_plan>{}</sql_plan>\n\n第二段。"
    cleaned = svc.sanitize_assistant_content(raw)
    assert "\n\n\n" not in cleaned
    assert "第一段。" in cleaned
    assert "第二段。" in cleaned


def test_sanitize_user_content_only_trims():
    assert svc.sanitize_user_content("  原始提问  \n") == "原始提问"
    assert svc.sanitize_user_content(None) == ""


def test_status_derivation():
    class Row:
        status = "active"
        expires_at = NOW - timedelta(minutes=1)

    assert svc.derive_share_status(Row(), now=NOW) == "expired"
    Row.status = "revoked"
    assert svc.derive_share_status(Row(), now=NOW) == "revoked"


def test_reachability():
    class Row:
        status = "active"
        expires_at = None

    assert svc.is_share_reachable(Row(), now=NOW) is True
    Row.expires_at = NOW + timedelta(seconds=1)
    assert svc.is_share_reachable(Row(), now=NOW) is True
    Row.expires_at = NOW
    assert svc.is_share_reachable(Row(), now=NOW) is False
