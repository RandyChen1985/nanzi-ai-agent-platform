"""用户管理的「设置密码」必须真的弹出弹窗。

线上现象：行内「⋯ → 设置密码」点了没反应。原因是内嵌弹窗被替换成共用组件
`PasswordResetDialog` 时，`Users.vue` 只改了模板块，忘了 `import`——Vue 把未注册
的 `<PasswordResetDialog>` 当成未知组件静默渲染为空，点击只是把一个 ref 置 true，
画面上什么都没有，控制台也只有一条 warning。

契约要同时锁住三件事，否则下次还可能只改一半：

1. 模板里用的组件必须真的被 import；
2. 弹窗挂在真实状态上（`showSetPasswordDialog && userToSetPassword`）；
3. 用户页与部门成员页共用同一个组件，不各写一份密码规则。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS = ROOT / "frontend/src/views/Users.vue"
MEMBERS_PANEL = ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue"

pytestmark = pytest.mark.no_infrastructure


def _imported_names(source: str) -> set[str]:
    names = set(re.findall(r"import\s+([A-Za-z0-9_]+)\s*(?:,|from)", source))
    for block in re.findall(r"import\s*\{([^}]*)\}\s*from", source):
        for item in block.split(","):
            name = item.strip().split(" as ")[-1].strip()
            if name:
                names.add(name)
    return names


def test_users_page_imports_the_shared_password_dialog():
    source = USERS.read_text(encoding="utf-8")

    assert "<PasswordResetDialog" in source, "模板里确实用了这个组件"
    assert "PasswordResetDialog" in _imported_names(source), (
        "模板用了 PasswordResetDialog 却没有 import——Vue 会静默渲染成空，"
        "表现就是「设置密码点了没反应」"
    )
    assert re.search(
        r'import\s+PasswordResetDialog\s+from\s+["\']\.\./components/PasswordResetDialog\.vue["\']',
        source,
    )


def test_set_password_dialog_is_bound_to_real_state():
    source = USERS.read_text(encoding="utf-8")
    dialog = source.split("<PasswordResetDialog", 1)[1].split("/>", 1)[0]

    assert 'v-if="showSetPasswordDialog && userToSetPassword"' in dialog
    assert ':display-name="userToSetPassword.user_name"' in dialog
    assert '@cancel="closeSetPasswordDialog"' in dialog
    assert '@submit="executeSetPassword"' in dialog


def test_row_menu_opens_the_dialog_for_the_selected_user():
    source = USERS.read_text(encoding="utf-8")
    menu_item = source.split("设置密码", 1)[0].rsplit("@click", 1)[1]

    assert "openSetPasswordDialog(openRowMenuUser)" in menu_item
    assert "const openSetPasswordDialog = (user: any)" in source
    assert "showSetPasswordDialog.value = true" in source


def test_members_panel_shares_the_same_dialog_component():
    """部门成员页与用户页共用一份密码规则，不能一边有规则说明一边只有裸输入框。"""
    source = MEMBERS_PANEL.read_text(encoding="utf-8")

    assert "<PasswordResetDialog" in source
    assert "PasswordResetDialog" in _imported_names(source)
