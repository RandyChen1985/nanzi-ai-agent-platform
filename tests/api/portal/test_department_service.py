"""部门服务：树路径、主部门规则、删除保护。

设计取舍：把**易错的判定逻辑**提炼成纯函数（路径拼接、主部门决议、移出后的重选），
DB 交互留在 `DepartmentService` 里用少量假 Session 覆盖。理由：

- 这几条规则是主部门快照正确性的全部依据（spec §5），一旦写错会让 `users.dept_code`
  与实际归属不一致，进而影响 SQL 行级过滤；
- 纯函数可以穷举边界（空集合、非法主部门、path 排序），不必每次都造数据库状态。
"""

from __future__ import annotations

import pytest

from app.services.department_service import (
    choose_primary_after_removal,
    compute_department_path,
    resolve_primary_selection,
    subtree_like_prefix,
)

pytestmark = pytest.mark.no_infrastructure


# ---------------- 物化路径 ----------------

def test_compute_path_for_root_department():
    assert compute_department_path(None, 7) == "/7/"


def test_compute_path_for_child_department():
    assert compute_department_path("/1/7/", 23) == "/1/7/23/"


def test_compute_path_keeps_trailing_slash_so_like_prefix_is_safe():
    """末尾斜杠必须有：否则 LIKE '/1/7%' 会把 /1/70/ 误当成子部门。"""
    path = compute_department_path("/1/", 7)
    assert path.endswith("/")
    assert subtree_like_prefix(path) == "/1/7/%"


# ---------------- 主部门决议（spec §5） ----------------

def test_resolve_primary_defaults_to_first_when_not_specified():
    assert resolve_primary_selection([3, 5], None) == 3


def test_resolve_primary_keeps_explicit_choice():
    assert resolve_primary_selection([3, 5], 5) == 5


def test_resolve_primary_rejects_choice_outside_set():
    with pytest.raises(ValueError):
        resolve_primary_selection([3, 5], 9)


def test_resolve_primary_returns_none_for_empty_set():
    assert resolve_primary_selection([], None) is None


def test_resolve_primary_rejects_choice_when_set_empty():
    with pytest.raises(ValueError):
        resolve_primary_selection([], 9)


# ---------------- 移出主部门后的重选 ----------------

def test_choose_primary_after_removal_picks_shallowest_by_path():
    remaining = [(5, "/2/5/"), (3, "/1/3/"), (9, "/1/3/9/")]
    assert choose_primary_after_removal(remaining) == 3


def test_choose_primary_after_removal_returns_none_when_no_departments_left():
    assert choose_primary_after_removal([]) is None


def test_choose_primary_after_removal_is_stable_for_equal_paths():
    """路径相同（同层）时按 id 升序，避免结果随查询顺序变化。"""
    assert choose_primary_after_removal([(8, "/1/8/"), (4, "/1/4/")]) == 4


# ---------------- 删除保护 ----------------

class _FakeDept:
    id = 7


class _CountSession:
    """只回放 count 查询；`get` 固定返回一个假部门，让 delete 能走到保护判定。"""

    def __init__(self, child_count: int, member_count: int):
        self._counts = [child_count, member_count]
        self.executed: list = []

    async def get(self, model, pk):
        return _FakeDept()

    async def execute(self, stmt):
        self.executed.append(stmt)
        value = self._counts.pop(0)

        class _R:
            def scalar(_self):
                return value

        return _R()


async def test_delete_rejects_when_children_exist():
    from app.services.department_service import DepartmentService

    svc = DepartmentService(_CountSession(child_count=2, member_count=0))
    with pytest.raises(ValueError) as exc:
        await svc.delete(7)
    assert "子部门" in str(exc.value)


async def test_delete_rejects_when_members_exist():
    from app.services.department_service import DepartmentService

    svc = DepartmentService(_CountSession(child_count=0, member_count=5))
    with pytest.raises(ValueError) as exc:
        await svc.delete(7)
    assert "成员" in str(exc.value)


async def test_delete_rejects_children_before_members():
    """先报子部门：管理员应自底向上清理，先看到子部门更有指导性。"""
    from app.services.department_service import DepartmentService

    svc = DepartmentService(_CountSession(child_count=1, member_count=3))
    with pytest.raises(ValueError) as exc:
        await svc.delete(7)
    assert "子部门" in str(exc.value)
