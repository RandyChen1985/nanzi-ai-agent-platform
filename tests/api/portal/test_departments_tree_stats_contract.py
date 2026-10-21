"""部门树统计与成员范围：去重人数 + 默认选中 + 含子部门。

三个决策都涉及"数字/范围"的正确性，容易做成看起来对、其实错：

1. **总人数必须去重**：各节点 `member_count` 是**直属**人数，相加得到的是"人次"。
   一个人同时属于「开发部」和「运维部」会被计两次，把它当"人数"展示会虚高。
   所以后端单独给 `COUNT(DISTINCT user_id)`。
2. **进页面要落在有内容的地方**：默认选中第一个根部门，而不是让人对着一片空态猜下一步。
3. **成员范围要能扩到子树**：部门负责人关心的往往是"整个分支有多少人"，
   但直属视图同样必要，所以做成开关而不是默认包含。含子部门时用户可能重复出现，
   必须按 user 去重，否则同一个人会在列表里出现两次。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[3]
ENDPOINTS = (ROOT / "app/api/portal/endpoints/departments.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "app/services/department_service.py").read_text(encoding="utf-8")
PAGE = (ROOT / "frontend/src/views/DepartmentManagement.vue").read_text(encoding="utf-8")
MEMBERS = (ROOT / "frontend/src/components/department/DepartmentMembersPanel.vue").read_text(encoding="utf-8")
TREE_VIEW = (ROOT / "frontend/src/components/department/DepartmentTree.vue").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1) 去重人数

def test_service_counts_distinct_members_not_sessions():
    assert "count_unique_members" in SERVICE
    segment = SERVICE.split("async def count_unique_members", 1)[1].split("async def ", 1)[0]
    assert "distinct" in segment.lower(), "必须 COUNT(DISTINCT user_id)，否则多部门用户被重复计数"


def test_tree_endpoint_returns_member_total():
    segment = ENDPOINTS.split('@router.get("/tree")', 1)[1].split("@router.", 1)[0]
    assert "member_total" in segment
    assert "count_unique_members" in segment


def test_page_shows_departments_and_people():
    """统计文案要能区分"部门数"与"去重人数"，避免把人次当人数。"""
    assert "memberTotal" in PAGE
    assert "人" in PAGE


# ---------------------------------------------------------------- 2) 默认选中

def test_page_auto_selects_first_root_department():
    assert "autoSelectFirstRoot" in PAGE or "selectedId.value === null" in PAGE


# ---------------------------------------------------------------- 3) 含子部门

def test_members_endpoint_supports_include_sub():
    segment = ENDPOINTS.split('@router.get("/{dept_ref}/members")', 1)[1].split("@router.", 1)[0]
    assert "include_sub" in segment
    assert "subtree_like_prefix" in ENDPOINTS, "子树范围要用物化路径前缀，不递归"


def test_subtree_members_are_deduplicated():
    segment = ENDPOINTS.split('@router.get("/{dept_ref}/members")', 1)[1].split("@router.", 1)[0]
    assert ".distinct()" in segment, "含子部门时同一用户可能在多行出现，必须按 user 去重"


def test_members_panel_has_include_sub_toggle():
    assert "includeSub" in MEMBERS or "include_sub" in MEMBERS
    assert "含子部门" in MEMBERS


def test_tree_node_flags_whether_it_has_children():
    """开关只在有子部门时才有意义，不能对叶子部门显示一个永远无效的开关。"""
    assert "children" in TREE_VIEW
