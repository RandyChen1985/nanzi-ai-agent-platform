"""界面模式黑名单的前端契约。

只钉「改坏了会出事」的几条边界：

1. 可达集合必须是「用户模式菜单 ∪ 账户菜单能到的页面」——漏了 `/dashboard/personal`
   会让被禁用专业模式的人点个人中心就被踢回聊天页；
2. `/dashboard`（概览）只能精确匹配，否则 `/dashboard/users` 会被当成它的子路径放行；
3. 守卫里黑名单判断必须在权限判断之前，且只看「禁用专业模式」（禁用用户模式不改页面集合）；
4. 账户菜单两种模式都渲染；没有权限的那枚必须置灰禁用，不能直接藏起来——
   藏了等于告诉用户「平台没有这个功能」，他既不知道原因也不知道该找谁开。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"


def read(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


def block(source: str, start: str) -> str:
    """截取从 start 开始到下一个顶层右花括号的片段，用于断言函数体内部。"""
    index = source.index(start)
    tail = source[index:]
    end = tail.find("\n}")
    return tail[: end + 2] if end > 0 else tail


# ---------------- 可达集合 ----------------


def test_reachable_paths_union_menu_and_account_menu():
    source = read("constants/userModeMenu.ts")
    assert "export function collectUserModeReachablePaths" in source

    body = block(source, "export function collectUserModeReachablePaths")
    assert "collectUserModePaths" in body
    # 账户菜单里的「个人中心」不在侧栏菜单里，必须显式补进来
    assert "USER_MODE_EXTRA_REACHABLE_PATHS" in body
    assert "/dashboard/personal" in source


def test_reachable_check_treats_dashboard_as_exact_match():
    source = read("constants/userModeMenu.ts")
    body = block(source, "export function isUserModeReachable")
    # 概况页只精确匹配：写死这一条，防止有人改回「统一前缀匹配」
    assert "/dashboard" in body
    assert "!==" in body or "!==" in body


def test_reachable_check_supports_child_paths():
    source = read("constants/userModeMenu.ts")
    body = block(source, "export function isUserModeReachable")
    assert "startsWith" in body


# ---------------- 状态与守卫 ----------------


def test_use_ui_mode_exposes_blocked_state():
    source = read("composables/useUiMode.ts")
    assert "blocked_ui_modes" in source
    assert "export function getCachedUiModeBlocked" in source
    assert "allowedModes" in source
    assert "canSwitchMode" in source


def test_use_ui_mode_writes_blocked_back_to_login_snapshot():
    """守卫读的是登录快照（同步、零请求），接口回来后必须回写，否则刷新前一直按旧值判断。"""
    source = read("composables/useUiMode.ts")
    assert "blocked_ui_modes" in source
    body = block(source, "async function loadFromServer")
    assert "persistUserInfo" in body or "patchUserInfo" in body


def test_router_guard_blocks_pro_pages_before_permission_check():
    source = read("router/index.ts")
    guard = source[source.index("router.beforeEach") :]

    assert "getCachedUiModeBlocked" in guard
    assert "isUserModeReachable" in guard

    blocked_at = guard.index("getCachedUiModeBlocked")
    perm_at = guard.index("const userModePass")
    assert blocked_at < perm_at


def test_router_guard_only_restricts_when_pro_is_blocked():
    source = read("router/index.ts")
    guard = source[source.index("router.beforeEach") :]
    body = guard[guard.index("getCachedUiModeBlocked") : guard.index("const userMenus")]
    assert "includes('pro')" in body or 'includes("pro")' in body


# ---------------- 端上展示 ----------------


def test_account_menu_shows_both_modes_and_disables_blocked_one():
    """两种模式常显：没权限的那枚置灰不可点，而不是消失。"""
    source = read("components/layout/AccountMenu.vue")
    assert "allowedModes" in source
    # 两个模式都进 v-for，不能先按 allowedModes 过滤再渲染
    assert 'v-for="option in modeOptions"' in source
    assert "isModeAllowed" in source
    # 真 disabled，而不是「能点但点了没反应」
    assert ':disabled="!isModeAllowed(option.value)"' in source
    assert "cursor-not-allowed" in source
    # 原来「只剩一种就换成只读卡片」的分支已经去掉
    assert "已固定" not in source


def test_personal_center_shows_disabled_reason():
    source = read("views/PersonalCenter.vue")
    assert "uiModeBlocked" in source or "blockedUiModes" in source or "uiModeAllowed" in source
    assert "管理员已禁用" in source or "已被管理员禁用" in source


def test_roles_dialog_has_ui_mode_section():
    source = read("views/Roles.vue")
    assert "blocked_ui_modes" in source
    assert "界面模式" in source
    # 管理员角色两个开关都锁死
    assert "isAdminRole" in source or "'admin'" in source or '"admin"' in source
    # 双禁时必须拦在保存前
    assert "至少保留一种" in source or "不能同时禁用" in source
