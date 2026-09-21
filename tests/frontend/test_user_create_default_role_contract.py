"""新增用户默认勾选 `default` 业务角色的契约（源码级）。

背景：平台约定普通用户默认归属于 `default` 业务角色，而新增用户弹窗的业务角色
列表此前始终为空，管理员每次都要手动勾选。这里锁定三点：

1. 新增弹窗用 `findAutoCheckedRoleIds()` 预置 `role_ids`，而不是空数组；
2. 该函数按角色名称或编码（忽略大小写、去首尾空格）匹配 `default`；
3. 角色列表尚未返回时先补齐再勾选，避免首次进入页面立刻点「新增」漏勾。

同时锁定编辑用户仍沿用该用户已有的 `role_ids`，不被默认勾选逻辑影响。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
USERS_VUE = ROOT / "frontend/src/views/Users.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return USERS_VUE.read_text(encoding="utf-8")


def _helper_body() -> str:
    """截取 `findAutoCheckedRoleIds` 函数体。"""
    return _source().split("const findAutoCheckedRoleIds = (): number[] => {", 1)[1].split(
        "\n};", 1
    )[0]


def _create_dialog_opener() -> str:
    """截取 `openCreateDialog` 函数体。"""
    return _source().split("const openCreateDialog = async () => {", 1)[1].split(
        "const editUser = async", 1
    )[0]


def _edit_user_opener() -> str:
    """截取 `editUser` 函数体。"""
    return _source().split("const editUser = async", 1)[1].split("const closeDialogs", 1)[0]


def test_create_dialog_preselects_default_role():
    """新增弹窗的业务角色必须是默认勾选结果，而不是空数组。"""
    opener = _create_dialog_opener()

    assert "role_ids: findAutoCheckedRoleIds()," in opener
    assert "role_ids: []," not in opener


def test_default_role_keys_match_name_or_code_case_insensitively():
    """`default` 需按名称或编码匹配，并忽略大小写与首尾空格。"""
    helper = _helper_body()

    assert 'AUTO_CHECKED_ROLE_KEYS = ["default"]' in _source()
    assert 'String(role?.code ?? "").trim().toLowerCase()' in helper
    assert 'String(role?.name ?? "").trim().toLowerCase()' in helper
    assert "AUTO_CHECKED_ROLE_KEYS.includes(code)" in helper
    assert "AUTO_CHECKED_ROLE_KEYS.includes(name)" in helper
    assert ".map((role: any) => role.id)" in helper


def test_create_dialog_loads_roles_before_preselecting():
    """角色列表为空时先拉取，保证首次进入页面立刻点「新增」也能勾上默认角色。"""
    opener = _create_dialog_opener()

    assert "if (businessRoles.value.length === 0)" in opener
    assert "await fetchBusinessRoles();" in opener


def test_edit_user_keeps_existing_role_ids():
    """编辑用户沿用已有角色，不受默认勾选逻辑影响。"""
    opener = _edit_user_opener()

    assert "role_ids: user.role_ids || []," in opener
    assert "findAutoCheckedRoleIds" not in opener
