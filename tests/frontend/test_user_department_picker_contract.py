"""用户侧部门交互契约：选择器替换自由输入、列表按部门筛选。

原先「部门代码」「组织全路径」是两个无校验的自由输入框，而这两个值会被 SQL 行级
过滤当真。这里锁住三件事：

1. Users.vue 不再直接编辑 dept_code / org_path（快照由后端按主部门派生）；
2. 部门归属通过独立的 `PUT /users/{id}/departments` 整集提交；
3. 列表提供部门维度筛选（dept_id + include_sub）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USERS_VUE = ROOT / "frontend/src/views/Users.vue"
PICKER = ROOT / "frontend/src/components/department/DepartmentPicker.vue"


def _users() -> str:
    return USERS_VUE.read_text(encoding="utf-8")


def test_free_text_department_inputs_are_gone():
    src = _users()
    assert 'v-model="formData.dept_code"' not in src
    assert 'v-model="formData.org_path"' not in src


def test_department_picker_is_wired_to_ids_and_primary():
    src = _users()
    assert "DepartmentPicker" in src
    assert 'v-model="formData.dept_ids"' in src
    assert 'v-model:primary-id="formData.primary_dept_id"' in src


def test_department_assignment_submitted_via_dedicated_endpoint():
    """部门归属是整集语义，走独立接口；不混进用户资料的 PUT。"""
    src = _users()
    assert "/departments`" in src or "/departments\"" in src
    segment = src.split("更新成功", 1)[0]
    assert "management/users/${editingUserId.value}/departments" in segment


def test_update_payload_no_longer_writes_snapshots():
    """dept_code / org_path 是派生快照，前端提交里不能出现。"""
    src = _users()
    segment = src.split("const updatePayload = {", 1)[1].split("};", 1)[0]
    assert "dept_code" not in segment
    assert "org_path" not in segment


def test_list_has_department_filter():
    src = _users()
    assert "deptFilter" in src
    assert "includeSub" in src
    assert "params.dept_id" in src
    assert "params.include_sub" in src


def test_picker_blocks_disabled_departments():
    src = PICKER.read_text(encoding="utf-8")
    assert "node.status === 0" in src
    # 主部门必须始终是已选部门之一
    assert "update:primaryId" in src


def test_picker_supports_keyword_search():
    """部门数可能上百，靠缩进逐层找不现实，搜索框不能被删。

    注意展示路径不能用 `node.path`——那是物化路径（`/1/7/23/`，存部门 id），
    给人看的是按名称拼出来的层级。
    """
    src = PICKER.read_text(encoding="utf-8")
    assert "data-department-search" in src
    assert "keyword" in src
    assert "filtered" in src
    # 名称与编码都要能搜到
    assert "row.node.name" in src and "row.node.code" in src
    # 搜索态下缩进归零，改由父级名称补层级信息
    assert "isSearching" in src
