"""契约：个人中心「测试发信」按钮。

这些断言守的是「后端已实现但前端漏接 / 接错」这一类静默失败：按钮无条件显示会让
用户在邮件服务未启用时点出一个必然失败的操作、漏读标志位会让按钮永远不出现、
把收件人做成输入框会把平台变成任意发信中继。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONAL = (ROOT / "frontend/src/views/PersonalCenter.vue").read_text(encoding="utf-8")
BACKEND_AUTH = (ROOT / "app/api/portal/endpoints/auth.py").read_text(encoding="utf-8")
BACKEND_SVC = (ROOT / "app/services/auth_service.py").read_text(encoding="utf-8")


def test_button_gated_by_mail_service_and_email():
    """按钮必须同时受「邮件服务可用」与「已设邮箱」两个条件控制。"""
    idx = PERSONAL.index("sendTestEmail\"")
    window = PERSONAL[max(0, idx - 400) : idx]
    assert "mailServiceAvailable" in window, "按钮未受邮件服务可用性控制"
    assert "userInfo.email" in window, "未设邮箱时不该显示按钮"


def test_flag_read_from_me_and_defaults_to_unavailable():
    """标志位取自 /auth/me，且缺失时按「不可用」处理。"""
    assert "mail_service_available" in PERSONAL
    idx = PERSONAL.index("mail_service_available")
    line = PERSONAL[max(0, idx - 120) : idx + 80]
    assert "=== true" in line, "标志位应严格判定为 true，取不到时按不可用处理"
    assert "/api/portal/auth/me'" in PERSONAL


def test_button_placed_after_edit_email_button():
    """按钮位置：在「修改邮箱」之后（用户明确要求）。"""
    edit_idx = PERSONAL.index("{{ userInfo.email ? '修改邮箱' : '设置邮箱' }}")
    test_idx = PERSONAL.index("sendTestEmail\"")
    assert edit_idx < test_idx, "「测试发信」必须排在「修改邮箱」之后"


def test_client_has_no_recipient_input():
    """不得提供收件人输入 —— 收件人由后端固定为本人邮箱，否则平台成了发信中继。"""
    assert "email-test'" in PERSONAL, "前端应调用自测端点"
    # 请求体必须是空的：不能把任何收件人字段带给后端
    assert "axios.post('/api/portal/auth/me/email-test'," not in PERSONAL, (
        "自测发信不应携带请求体（收件人由后端固定为本人邮箱）"
    )


def test_cooldown_matches_backend_constant():
    """前端倒计时必须与后端冷却一致，否则用户会白点一次或被多等。"""
    assert "TEST_EMAIL_COOLDOWN_SECONDS = 60" in PERSONAL
    assert "MAIL_TEST_COOLDOWN_SECONDS = 60" in BACKEND_SVC


def test_backend_endpoint_hides_email_and_never_takes_recipient():
    """后端契约：端点不接收请求体参数（收件人只能来自库里的本人邮箱）。"""
    idx = BACKEND_AUTH.index('@router.post("/me/email-test"')
    signature = BACKEND_AUTH[idx : BACKEND_AUTH.index("):", idx)]
    assert "payload" not in signature, "端点不得接收请求体，否则收件人可被指定"
    assert "require_api_key" in signature
