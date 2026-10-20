"""个人中心「我的资料」要显示部门，而且显示名字、不显示机器路径。

背景：智能体回答"我是什么部门的"时会指路「个人中心 → 我的资料」，但那块只有
头像、姓名、角色、用户 ID、创建时间、上次登录——根本没有部门。普通用户进不了
用户管理，等于没有任何地方能自助确认自己的部门归属。

显示的是部门名（总部、开发部），不是 `dept_code='main'` 也不是 `org_path='/main/dev/'`：
后者是给程序看的，摆到个人中心只会让人更糊涂。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "frontend/src/views/PersonalCenter.vue").read_text(encoding="utf-8")


pytestmark = pytest.mark.no_infrastructure


def _profile_block() -> str:
    """账号基本信息区：个人身份资料的展示块。"""
    return SOURCE.split("账号基本信息", 1)[1].split("邮箱", 1)[0]


def test_profile_shows_department_names():
    assert "dept_names" in _profile_block(), "资料区要展示部门名列表"


def test_profile_never_shows_machine_paths():
    """`org_path`（/main/dev/）与 `dept_code`（main）是机器格式，不进个人中心。"""
    # 只看绑定用法：注释里解释"为什么不显示 org_path"是应该保留的
    assert "userInfo.org_path" not in SOURCE
    assert "userInfo.dept_code" not in SOURCE


def test_missing_department_has_an_explicit_fallback():
    """没有部门的人不该看到一块空白，也不能因此报错。"""
    assert "未设置部门" in SOURCE


def test_primary_department_is_visually_distinct():
    """主部门与兼职部门要能区分开——多部门场景下人最关心"主"是哪个。"""
    block = _profile_block()
    assert "idx === 0" in block or "index === 0" in block


def test_departments_render_without_native_title_only():
    """部门名直接列出来，不靠原生 title 才看得到（移动端 hover 不出来）。"""
    block = _profile_block()
    assert "dept_names" in block
    assert "v-for" in block
