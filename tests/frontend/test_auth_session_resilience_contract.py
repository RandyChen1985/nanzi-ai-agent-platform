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
    """管理后台页面必须复用 utils/axios，以统一携带同源会话 Cookie 与 401 处理。"""
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


def test_global_axios_does_not_inject_credentials_from_local_storage():
    """门户凭据只走同源 HttpOnly Cookie，全局拦截器不得从 localStorage 注入凭据。

    凭据不进入 JS 可读的存储，XSS 就无法带走会话；因此也不再需要按 /embed/ 路径区分。
    """
    source = _read("frontend/src/main.ts")

    assert "config.headers['X-API-Key']" not in source
    assert "localStorage.getItem('api_key')" not in source
    assert "axios.interceptors.request" not in source


def test_authenticated_axios_instance_does_not_inject_api_key():
    """utils/axios 同样不再注入 X-API-Key；嵌入场景所需的 Bearer 令牌保留。"""
    source = _read("frontend/src/utils/axios.ts")

    # 不注入（赋值）X-API-Key；读取并尊重调用方已设置的头部是允许的
    assert "config.headers['X-API-Key'] =" not in source
    assert "localStorage.getItem('api_key')" not in source
    # 同源 Cookie 要能自动携带，这是去掉显式凭据注入的前提
    assert "withCredentials: true" in source


def test_both_axios_401_interceptors_clear_the_same_credentials():
    """两个 401 拦截器清理的凭据必须一致，避免残留半登录态。"""
    for path in ("frontend/src/main.ts", "frontend/src/utils/axios.ts"):
        source = _read(path)

        for key in ("api_key", "user_info", "admin_token", "yovole_token"):
            assert f"localStorage.removeItem('{key}')" in source, (path, key)


def test_persist_user_info_purges_legacy_credential_copies():
    """写入会话快照时应顺手清除旧版本残留在本地的凭据副本。

    改造前登录/嵌入流程会把凭据写入 localStorage，已登录的老用户浏览器里可能仍有存量。
    这些键如今已无任何写入方（前端也不再读取），因此统一在写入快照时清掉，避免长期滞留。
    清理放在 persistUserInfo 而非单个页面：它是会话落盘的统一入口，覆盖全部调用方。
    """
    source = _read("frontend/src/utils/userSession.ts")
    persist_section = source.split("export function persistUserInfo", 1)[1].split(
        "export function", 1
    )[0]

    for key in ("api_key", "admin_token", "yovole_token"):
        assert f"localStorage.removeItem('{key}')" in persist_section, key

    # 登录阶段绝不能清除 admin_token Cookie——它现在是有效的会话凭据，由后端下发。
    #
    # 只检查**代码行**：`persist_section` 会一路截到下个 `export function` 之前，
    # 因而包含紧随其后的 clearUserSession 的 JSDoc；而说明「此处不应操作
    # document.cookie」的注释本身必然含有该字面量，直接做子串匹配会被自己的文档误伤。
    code_text = "\n".join(
        line
        for line in persist_section.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )
    assert "document.cookie" not in code_text
