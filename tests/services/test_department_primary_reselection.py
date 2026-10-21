"""移出主部门后重选主部门：必须按**层级深度**挑，不能按路径字符串排序。

`choose_primary_after_removal` 的注释写的是"取层级最浅（path 最短）的"，但实现
用的是 `sort(key=lambda item: (item[1], item[0]))`——对 `/1/2/` 这种字符串做字典序
比较，和深度没有关系：

    "/1/2/" < "/15/"   → True（第 3 个字符 '/' < '5'）

于是 `15` 号这个**根部门**会输给 `2` 号这个**二级部门**。部门数量一旦到两位数
（id 出现两位），这个错误就会真实发生——把人的主部门设成更深的那一级，正好和
"往上走一级"的设计意图相反。
"""

from __future__ import annotations

import pytest

from app.services.department_service import choose_primary_after_removal

pytestmark = pytest.mark.no_infrastructure


def test_picks_shallowest_when_ids_have_different_lengths():
    """回归用例：`/15/`（根，深度 1）必须胜过 `/1/2/`（深度 2）。"""
    assert choose_primary_after_removal([(15, "/15/"), (2, "/1/2/")]) == 15


def test_picks_shallowest_among_deep_candidates():
    deep = (5, "/1/2/3/4/5/")
    shallow = (100, "/100/")
    assert choose_primary_after_removal([deep, shallow]) == 100


def test_same_depth_falls_back_to_id_order():
    """同层时结果要稳定：按 id 升序，避免每次重选出来的主部门飘来飘去。"""
    assert choose_primary_after_removal([(9, "/1/9/"), (3, "/1/3/")]) == 3


def test_empty_returns_none():
    assert choose_primary_after_removal([]) is None


def test_ignores_blank_path_segments():
    """畸形的空路径段不能把深度算多。"""
    assert choose_primary_after_removal([(2, "/1//2/"), (7, "/7/")]) == 7
