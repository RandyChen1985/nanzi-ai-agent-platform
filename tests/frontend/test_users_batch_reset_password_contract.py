"""用户管理「批量重置密码（邮件下发随机密码）」的前后端契约。

这条链路的风险不在功能，而在**顺序与开关**：

1. 平台邮件服务没启用时，按钮必须不可点，且后端也要拒绝——只灰前端挡不住直接
   调接口的脚本，最后会变成「密码改了、邮件永远发不出去」的孤儿账号。
2. 后端必须**先发信、后落库**。反过来（先改密再发信）遇到 SMTP 抖动就会留下
   「密码已改、用户没收到」的账号：用户不知道该输什么，管理员也不知道。
3. 没填邮箱的用户要跳过而不是让整批失败，且结果里点名，管理员才能补邮箱重试。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW_PATH = "frontend/src/views/Users.vue"
BACKEND_PATH = "app/api/portal/endpoints/management.py"
MAIL_PATH = "app/services/email_delivery_service.py"
AUTH_PATH = "app/services/auth_service.py"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_users_view_exposes_mail_reset_entry_gated_by_mail_status():
    view = _source(VIEW_PATH)

    assert "批量重置密码" in view
    assert "openBatchResetDialog" in view
    assert "executeBatchReset" in view
    # 按钮可用性来自后端状态，不是前端猜配置
    assert "mailServiceAvailable" in view
    assert "mailServiceReason" in view
    assert ':disabled="!mailServiceAvailable' in view
    assert "/api/portal/management/users/mail-service-status" in view


def test_users_view_confirms_before_resetting():
    view = _source(VIEW_PATH)

    assert "showBatchResetDialog" in view
    assert "确认批量重置密码" in view
    # 必须说清三件事：随机密码不显示、原密码失效、会话被踢
    assert "密码不会显示在这个页面上" in view
    assert "原密码立即失效" in view
    assert "已登录会话会被踢出" in view


def test_reset_result_names_users_skipped_for_missing_email():
    view = _source(VIEW_PATH)

    assert "skippedNoEmail" in view
    assert "个用户未填邮箱，已跳过" in view
    assert "补上邮箱后可再次勾选他们重置" in view
    assert "batchResetResult.failed" in view


def test_backend_reset_endpoint_sends_mail_before_writing_password():
    backend = _source(BACKEND_PATH)

    assert "async def batch_reset_user_password(" in backend
    assert '"/users/batch-reset-password"' in backend
    assert '"/users/mail-service-status"' in backend
    assert "generate_compliant_password(" in backend
    assert "skipped_no_email" in backend

    body = backend.split("async def batch_reset_user_password(")[1].split("\n@router.")[0]
    # 顺序即正确性：先发信（失败就跳过），成功之后才落库
    assert body.index("_send_password_reset_mail(") < body.index("set_user_password(")
    # 邮件服务未启用时不查库
    assert "load_global_smtp_settings" in body


def test_generated_password_and_mail_template_exist():
    auth = _source(AUTH_PATH)
    mail = _source(MAIL_PATH)

    assert "def generate_compliant_password(" in auth
    # 生成器复用同一条规则做兜底校验，不是自己另写一套
    assert "validate_password_complexity(candidate" in auth

    assert "def build_password_reset_mail(" in mail
    assert "临时密码" in mail
    assert "请在首次登录后立即修改密码" in mail
