"""「含子部门」视图下，TEAM 级操作只能作用在**直属**成员身上。

父部门打开「含子部门」后，列表里混着子部门的成员。而「设为主部门」「移出」两个
操作调用的是当前选中部门的接口（按 dept.id 校验归属），对"只属于子部门"的人：

- 设为主部门 → 后端 400「该用户不在此部门中」；
- 移出 → 关系表里没有当前部门的行，移出 0 行，而列表因为包含子树，人还在原地，
  看起来像"点了没反应"。

所以后端要在成员列表里标出 `is_direct`，前端据此不给这两个操作，批量移出也要把
这些人排除掉（否则一整批里混一个就白跑）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = (ROOT / "app/api/portal/endpoints/departments.py").read_text(encoding="utf-8")
PANEL = (ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue").read_text(
    encoding="utf-8"
)

pytestmark = pytest.mark.no_infrastructure


def test_member_payload_marks_direct_membership():
    assert '"is_direct"' in BACKEND, "成员列表要告诉前端该人是否直属当前部门"
    # 直属视图（include_sub=False）里每个人必然直属，别为它多发查询
    assert "direct_user_ids" in BACKEND


def test_dept_scoped_row_actions_require_direct_membership():
    assert 'v-if="canEdit && member.is_direct && !member.is_primary"' in PANEL
    assert 'v-if="canEdit && member.is_direct"' in PANEL


def test_batch_remove_skips_subtree_only_members():
    body = PANEL.split("const requestRemove", 1)[1].split("const cancelRemove", 1)[0]
    assert "is_direct" in body, "批量移出要过滤掉非直属成员"
    assert "属于子部门" in body, "被跳过的人要给一句说明，否则用户以为操作丢了"


def test_user_level_actions_stay_available_for_subtree_members():
    """禁用 / 重置密码 / 编辑是用户级操作，与部门归属无关，不能跟着被隐藏。"""
    assert 'v-if="canEditUser"' in PANEL
    for action in ("toggleStatus", "openPasswordDialog", "openUserEditor"):
        assert f"@click=\"{action}(member)\"" in PANEL
