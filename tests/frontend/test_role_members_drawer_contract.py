"""角色成员抽屉的前端契约。

设计见 `docs/superpowers/specs/2026-10-20-role-member-drawer-design.md`。这里钉住的是
几条**容易在后续改动里被悄悄破坏**的约束，而不是具体排版：

1. 移出必须走只做减法的 `/users/remove`，不能退回整集替换（并发会互相覆盖）；
2. 动作按权限点分别收敛：角色权限管成员关系，用户权限管账号状态，不能混用；
3. `miniprogram_user` 是静默登录自动加回的角色，移出必须禁用；
4. 角色页要真的挂上抽屉，用户数真的可点；
5. 用户页要能吃 `?q=` 深链，否则抽屉里的「去用户管理」跳过去是空搜索。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRAWER_PATH = ROOT / "frontend/src/components/system/RoleMembersDrawer.vue"
ROLES = (ROOT / "frontend/src/views/Roles.vue").read_text(encoding="utf-8")
USERS = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")


def _drawer() -> str:
    assert DRAWER_PATH.exists(), "角色成员抽屉必须独立成组件，不能继续塞进 Roles.vue"
    return DRAWER_PATH.read_text(encoding="utf-8")


def test_drawer_removes_through_subtractive_endpoint():
    """移出走 remove 端点；抽屉里不得再出现整集替换的提交。"""
    drawer = _drawer()

    assert "/users/remove" in drawer
    assert "user_ids: ids" in drawer or "user_ids:" in drawer
    # 整集替换只属于父级的「分配用户」双栏弹窗，抽屉不该再写一份
    assert "await axios.post(`/api/portal/roles/${props.role.id}/users`" not in drawer


def test_drawer_separates_role_and_user_permissions():
    """成员关系归 element:role:edit，账号状态归 element:user:edit。"""
    drawer = _drawer()

    assert "element:role:edit" in drawer
    assert "element:user:edit" in drawer
    assert "canEditRole" in drawer
    assert "canEditUser" in drawer


def test_drawer_hides_actions_behind_permission_checks():
    """每个动作按钮都要有权限条件，避免"看得到点了 403"。"""
    drawer = _drawer()

    for action in ("removeMembers", "toggleStatus", "openPasswordDialog"):
        assert action in drawer, f"抽屉缺少动作 {action}"

    # 模板里必须用 v-if 按权限收敛，而不是只声明了 canEditRole/canEditUser 却没用
    assert 'v-if="canEditRole' in drawer
    assert 'v-if="canEditUser' in drawer
    assert drawer.count('v-if="canEdit') >= 4, "移出/添加/启停/密码都要各自受权限控制"


def test_miniprogram_role_cannot_remove_members():
    """小程序角色移出无效，必须在 UI 层禁用并说明原因。"""
    drawer = _drawer()

    assert "miniprogram_user" in drawer
    assert "可移除" in drawer or "isRemovable" in drawer or "移出无效" in drawer


def test_system_role_removal_explains_consequence():
    """default/admin/system 允许移出，但必须二次确认说明后果。

    确认必须走页面内弹窗：项目契约（tests/frontend/test_no_native_confirmation_contract.py）
    对原生 `confirm()` 零容忍——原生弹窗阻塞主线程、无法跟随主题，在内嵌 WebView 里还可能被拦截。
    """
    drawer = _drawer()

    assert "没有任何业务角色" in drawer
    assert "ConfirmModal" in drawer
    assert "window.confirm" not in drawer
    assert "confirm(" not in drawer


def test_roles_view_wires_drawer_and_clickable_count():
    """角色页挂上抽屉，用户数是真的按钮而不是徽标。"""
    assert "RoleMembersDrawer" in ROLES
    assert "openMembersDrawer" in ROLES
    assert "showMembersDrawer" in ROLES
    # 入口必须绑在用户数上，且桌面与移动端都有
    assert ROLES.count("openMembersDrawer(") >= 3, "桌面入口、移动端入口与打开方法本身都要有"


def test_users_view_reads_deep_link_query():
    """用户页要吃 ?q=，否则抽屉里的跳转落不到搜索结果。"""
    assert "route.query.q" in USERS
    assert "useRoute" in USERS
