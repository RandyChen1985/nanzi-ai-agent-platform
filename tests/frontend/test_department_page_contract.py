"""部门管理页契约：权限收敛、零原生弹窗、成员走增量接口。

项目硬约束：`frontend/src/**/*.vue` 内不允许出现原生 `confirm()/alert()/prompt()`
（由 `test_no_native_confirmation_contract.py` 全量扫描）。部门页要在"删除部门"和
"移出成员"两个破坏性操作上给出确认，因此必须复用 `ConfirmModal`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/DepartmentManagement.vue"
TREE = ROOT / "frontend/src/components/department/DepartmentTree.vue"
INFO = ROOT / "frontend/src/components/department/DepartmentInfoPanel.vue"
MEMBERS = ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue"
PICKER = ROOT / "frontend/src/components/department/DepartmentPicker.vue"

ALL_FILES = (PAGE, TREE, INFO, MEMBERS, PICKER)


def test_all_component_files_exist():
    for path in ALL_FILES:
        assert path.exists(), f"缺少文件 {path}"


def test_write_actions_gated_by_dept_edit_permission():
    page = PAGE.read_text(encoding="utf-8")
    assert "hasPermission('element:dept:edit')" in page
    assert "hasPermission('element:dept:delete')" in page
    assert 'v-if="canEdit"' in page
    # 树与信息面板都接收只读标记，只读时不得渲染写入口
    assert ":readonly=\"!canEdit\"" in page


def test_readonly_mode_is_explained():
    page = PAGE.read_text(encoding="utf-8")
    assert "只读模式" in page


def test_no_native_dialogs_in_any_department_file():
    for path in ALL_FILES:
        source = path.read_text(encoding="utf-8")
        assert "window.confirm" not in source, f"{path.name} 用了原生 confirm"
        assert "window.alert" not in source, f"{path.name} 用了原生 alert"
        assert "window.prompt" not in source, f"{path.name} 用了原生 prompt"


def test_destructive_actions_use_confirm_modal():
    page = PAGE.read_text(encoding="utf-8")
    members = MEMBERS.read_text(encoding="utf-8")
    assert "ConfirmModal" in page, "删除部门需要确认弹窗"
    assert "ConfirmModal" in members, "移出成员需要确认弹窗"


def test_members_panel_uses_incremental_endpoints():
    members = MEMBERS.read_text(encoding="utf-8")
    assert "/members/remove" in members
    assert "/members/primary" in members
    # 增量语义：加人与移人分别调接口，不存在"提交整集"的调用
    assert "user_ids: candidateSelected.value" in members
    assert "user_ids: ids" in members


def test_members_panel_excludes_existing_members_from_candidates():
    members = MEMBERS.read_text(encoding="utf-8")
    assert "exclude_dept_id" in members


def test_tree_actions_are_reorder_and_create_child():
    tree = TREE.read_text(encoding="utf-8")
    for action in ("create-child", "move-up", "move-down", "delete"):
        assert action in tree


def test_page_loads_tree_from_departments_api():
    page = PAGE.read_text(encoding="utf-8")
    assert "/api/portal/departments/tree" in page
    assert "/api/portal/departments/reorder" in page


# ---------------------------------------------------------------- 权限收敛（读 vs 编辑 vs 删除）

def test_read_only_user_sees_no_write_controls():
    """没有 `element:dept:edit` 时，写操作是**隐藏**而不是置灰。

    只置灰会让人以为"点了可能能成"，然后反复点；隐藏才是明确的"你不该做这件事"。
    """
    page = PAGE.read_text(encoding="utf-8")
    # 顶部新建入口
    assert '<template v-if="canEdit">' in page
    # 树上操作（+ / ↑ / ↓ / ✕）
    assert ':readonly="!canEdit"' in page
    # 信息面板的保存/删除按钮整块跟着 readonly 走
    info = INFO.read_text(encoding="utf-8")
    assert '<div v-if="!readonly"' in info
    # 成员 tab 的添加/勾选/移除
    members = MEMBERS.read_text(encoding="utf-8")
    assert 'v-if="canEdit && deptId"' in members


def test_inputs_are_disabled_when_readonly():
    info = INFO.read_text(encoding="utf-8")
    assert info.count(':disabled="readonly"') >= 3


def test_tree_delete_button_requires_delete_permission():
    """`element:dept:delete` 与 `element:dept:edit` 是两码事。

    树上那个 ✕ 曾经只跟着 readonly 走：只有编辑权限、没有删除权限的人 hover 就能看到
    ✕，点下去才被后端 403。UI 不该暴露一个必然失败的操作。
    """
    tree = TREE.read_text(encoding="utf-8")
    assert "canDelete" in tree, "树需要独立的删除权限入参"
    x_button = tree.split('title="删除部门"', 1)[0].rsplit("<button", 1)[1]
    assert 'v-if="canDelete"' in x_button


def test_page_passes_delete_permission_to_tree():
    page = PAGE.read_text(encoding="utf-8")
    tree_usage = page.split("<DepartmentTree", 1)[1].split("/>", 1)[0]
    assert ':can-delete="canDelete"' in tree_usage


# ---------------------------------------------------------------- tab 顺序与默认页

def test_members_tab_is_first_and_default():
    """部门页的日常用途是"看这个部门有哪些人"，信息维护是低频动作。

    所以成员排第一、点开部门直接落到成员页；新建部门后仍跳到信息页（那时确实要补属性）。
    """
    page = PAGE.read_text(encoding="utf-8")
    assert page.index("label: '成员'") < page.index("label: '部门信息'")
    assert "ref<'info' | 'members'>('members')" in page


def test_new_department_still_lands_on_info_tab():
    """新建完成后不是"看人"，而是确认刚填的信息，所以这里保留显式切到 info。"""
    page = PAGE.read_text(encoding="utf-8")
    create_block = page.split("const submitCreate", 1)[1].split("const saveInfo", 1)[0]
    assert "activeTab.value = 'info'" in create_block


# ---------------------------------------------------------------- 重置密码复用公共组件

def test_password_reset_uses_shared_component():
    """密码弹窗必须与用户管理共用同一个组件。

    之前部门页自己写了两个裸输入框：规则（`checkPasswordPolicy`）虽然一致，
    但等保说明、实时合规指标、输入框染色全都缺，看起来像"另一套更宽松的规范"。
    """
    members = MEMBERS.read_text(encoding="utf-8")
    assert "PasswordResetDialog" in members
    assert "checkPasswordPolicy" not in members, "校验逻辑应留在公共组件内，面板不要重复实现"


# ---------------------------------------------------------------- 添加成员弹窗

def test_add_member_dialog_shows_enough_context_to_choose():
    """候选列表只给"账号 + 姓名"是不够的：同名、同邮箱前缀的账号很多，
    管理员还需要看到角色、是否禁用、以及**已经在哪些部门**，才判断得出该加谁。
    """
    members = MEMBERS.read_text(encoding="utf-8")
    assert "initialOf" in members, "缺少首字母头像，长列表很难扫读"
    assert "deptSummary" in members, "要显示该用户已在哪些部门，避免重复添加无依据"
    assert "管理员" in members and "禁用" in members


def test_add_member_dialog_has_bulk_and_empty_states():
    members = MEMBERS.read_text(encoding="utf-8")
    assert "toggleCandidateAll" in members, "缺少全选/取消全选"
    assert "没有可添加的用户" in members, "缺少空状态说明"
    assert "animate-spin" in members, "缺少加载态"


def test_add_member_dialog_keeps_incremental_payload():
    """美化不能顺手把增量语义改掉：这里仍然只提交本次勾选的 user_ids。"""
    members = MEMBERS.read_text(encoding="utf-8")
    assert "user_ids: candidateSelected.value" in members
