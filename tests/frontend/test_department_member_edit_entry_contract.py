"""部门成员列表要能直接进入该用户的编辑界面。

为什么不是在本页内嵌编辑弹窗：用户编辑表单有 760+ 行模板，还牵着权限 tab、
额度 tab、Agent 限定、API Key 等一串状态，把它抽成公用组件等于重写用户管理。
部门页已有「↗ 去用户管理」的先例，所以编辑走**深链**：跳过去并自动打开
那一行的编辑框，落点仍是唯一的实现，不会出现两套编辑逻辑各自长歪。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MEMBERS = (ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue").read_text(
    encoding="utf-8"
)
USERS = (ROOT / "frontend/src/views/Users.vue").read_text(encoding="utf-8")

pytestmark = pytest.mark.no_infrastructure


def test_members_panel_has_edit_entry():
    assert "openUserEditor" in MEMBERS, "成员行要有编辑入口"
    # 入口必须在操作列里，且和「去用户管理」分开——前者直接进编辑，后者只是查看
    assert "jumpToUser" in MEMBERS


def test_edit_entry_deep_links_with_edit_param():
    segment = MEMBERS.split("const openUserEditor", 1)[1].split("const ", 1)[0]
    assert "edit" in segment, "深链要带 edit 参数，用户页据此自动打开编辑框"
    assert "/dashboard/users" in segment


def test_edit_entry_respects_edit_permission():
    """没有编辑用户权限的人不该看到这个入口，否则点过去也是一片拒绝。"""
    idx = MEMBERS.index("openUserEditor(member)")
    assert "canEditUser" in MEMBERS[max(0, idx - 500):idx]


def test_users_view_opens_editor_from_query():
    assert "route.query.edit" in USERS, "用户页要消费 ?edit= 深链"
    assert "router.replace" in USERS, "打开后要清掉 query，否则刷新会重复弹窗"
    assert "useRouter" in USERS
