"""服务重启窗口期不应把用户误踢到登录页：会话清理只能由认证类失败触发。"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dashboard_auth_check_only_logs_out_on_auth_failure():
    """刷新时的 /me 校验：仅 401/403 清会话并跳登录，网络/5xx 保留会话。"""
    source = _read("frontend/src/views/Dashboard.vue")

    assert "isAuthFailure" in source
    assert "status === 401" in source
    assert "status === 403" in source
    assert "AUTH_CHECK_RETRY_DELAYS" in source
    assert "scheduleAuthCheckRetry" in source


def test_dashboard_does_not_clear_session_before_auth_failure_is_confirmed():
    """catch 分支必须先判定认证类失败，再清空 api_key，不能无条件清理。"""
    source = _read("frontend/src/views/Dashboard.vue")

    fetch_start = source.index("const fetchUserInfo")
    guard_index = source.index("if (isAuthFailure(e))", fetch_start)
    clear_index = source.index('localStorage.removeItem("api_key")', fetch_start)

    assert guard_index < clear_index


def test_admin_console_views_share_the_authenticated_axios_instance():
    """管理后台页面必须复用 utils/axios，才能自动携带 X-API-Key，而不是只靠 cookie。"""
    for name in ("Users.vue", "Roles.vue", "KnowledgeMetrics.vue", "MetadataTables.vue"):
        source = _read(f"frontend/src/views/{name}")

        assert "utils/axios" in source, name
        assert 'from "axios"' not in source, name
        assert "from 'axios'" not in source, name


def test_user_info_snapshot_never_persists_api_key():
    """user_info 只是身份/权限快照，不能携带 API Key，避免长期凭据在本地留多份副本。"""
    helper = ROOT / "frontend/src/utils/userSession.ts"

    assert helper.exists(), "缺少统一的本地会话写入入口"
    helper_source = helper.read_text(encoding="utf-8")
    assert "persistUserInfo" in helper_source
    assert "api_key" in helper_source

    for path in (
        "frontend/src/views/Login.vue",
        "frontend/src/views/Dashboard.vue",
        "frontend/src/views/NoPermission.vue",
        "frontend/src/views/Users.vue",
    ):
        source = _read(path)

        assert "persistUserInfo" in source, path
        assert "setItem('user_info'" not in source, path
        assert 'setItem("user_info"' not in source, path


def test_global_axios_injects_api_key_for_admin_requests():
    """全局 axios 也要补 X-API-Key，否则抽屉/弹窗组件只依赖 cookie，容易 401。"""
    source = _read("frontend/src/main.ts")

    assert "axios.interceptors.request.use" in source
    assert "config.headers['X-API-Key']" in source
    assert "localStorage.getItem('api_key')" in source
    # 嵌入页面的凭据由 EmbedChat 自行注入，全局兜底不能介入
    assert "!window.location.pathname.startsWith('/embed/')" in source


def test_both_axios_401_interceptors_clear_the_same_credentials():
    """两个 401 拦截器清理的凭据必须一致，避免残留半登录态。"""
    for path in ("frontend/src/main.ts", "frontend/src/utils/axios.ts"):
        source = _read(path)

        for key in ("api_key", "user_info", "admin_token", "yovole_token"):
            assert f"localStorage.removeItem('{key}')" in source, (path, key)
