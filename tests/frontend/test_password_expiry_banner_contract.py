"""密码到期横幅的渲染与数据契约。

横幅数据来自 `userInfo.password_info`（`/auth/me` 与本地快照同结构）。这里锁住：

1. 横幅只看 `password_info` 本身，不额外加「等接口」的门控——加了门控的话，
   接口一次不返回（或返回「还早」）横幅就彻底不出现；
2. 已过期状态强制常驻，不允许被关闭按钮隐藏；
3. 后端算「密码最后修改时间」不能用 `updated_at`：它是 onupdate，登录（写
   `last_login_at`）与改资料都会刷新它，会把到期天数重置成 0。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "frontend/src/views/Dashboard.vue"
NOTICE = ROOT / "frontend/src/utils/passwordExpiry.ts"
AUTH = ROOT / "app/api/portal/endpoints/auth.py"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard() -> str:
    return _read(DASHBOARD)


def test_banner_renders_from_password_info_only(dashboard: str) -> None:
    """不额外加门控：有 password_info 且触发阈值就直接显示。"""
    assert "const passwordNoticeReady" not in dashboard
    show_block = dashboard.split("const showPasswordExpireBanner", 1)[1].split("});", 1)[0]
    assert "passwordExpireNoticeData.value" in show_block
    # 模板里必须同时判横幅状态与数据，避免读空对象
    assert 'v-if="showPasswordExpireBanner && passwordExpireNoticeData"' in dashboard


def test_expired_banner_cannot_be_dismissed(dashboard: str) -> None:
    assert "if (passwordExpireNoticeData.value.isExpired) return true;" in dashboard
    assert "if (passwordExpireNoticeData.value?.isExpired) return;" in dashboard
    assert "dismiss_pwd_banner_" in dashboard


def test_backend_ignores_updated_at_for_password_age() -> None:
    """`updated_at` 是 onupdate，登录/改资料会刷新它，不能拿来算密码天数。"""
    auth = _read(AUTH)
    assert "dt = user_obj.password_updated_at or user_obj.created_at" in auth
    block = auth.split("if has_password and user_obj:", 1)[1].split("if dt:", 1)[0]
    assert "user_obj.updated_at" not in block


def test_notice_threshold_and_copy(dashboard: str) -> None:
    notice = _read(NOTICE)
    assert "PASSWORD_EXPIRY_THRESHOLD_DAYS = 7" in notice
    assert "resolvePasswordExpiryNotice" in dashboard
    # 剩 3 天走 warning 分支，文案要带具体天数
    assert "还有 ${daysUntil} 天即将过期" in notice
