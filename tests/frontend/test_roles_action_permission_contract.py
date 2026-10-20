"""角色管理页操作按钮必须按 element:* 权限收敛（源码级契约）。

背景：用户截图里，一个**没有**「编辑角色 / 删除角色」权限的账号，仍然在角色列表
操作列看到编辑、分配权限、分配用户、删除四个按钮，以及右上角「创建角色」。
后端 `app/api/portal/endpoints/roles.py` 的所有写接口都挂了
`require_permission("element", "element:role:edit")`，所以点了只会吃 403——
不是越权，是前端漏了守卫，属于「按钮在、点了报错」的 UX 缺陷。

这里锁定四件事：

1. 角色页接入 `useUser().hasPermission`，并按后端真实权限码声明
   `canEditRole`（element:role:edit）、`canDeleteRole`（element:role:delete）；
2. 创建 / 编辑 / 分配权限 / 分配用户 四个入口由 `canEditRole` 守卫，
   桌面表格与移动端卡片两处都要改（历史上多次只改一处）；
3. 删除入口由 `canDeleteRole` 守卫，并额外排除系统角色（default/admin/system）；
4. 无任何操作权限时不渲染空的操作列，显示占位符。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return ROLES_VUE.read_text(encoding="utf-8")


def _buttons(handler: str) -> list[str]:
    """返回所有 `@click="handler"` 所属 <button ...> 起始标签。"""
    source = _source()
    tags: list[str] = []
    cursor = 0
    needle = f'@click="{handler}'
    while True:
        try:
            idx = source.index(needle, cursor)
        except ValueError:
            return tags
        start = source.rindex("<button", 0, idx)
        end = source.index(">", idx) + 1
        tags.append(source[start:end])
        cursor = end


def _header() -> str:
    return _source().split('<div class="flex flex-col lg:flex-row', 1)[1].split(
        "<!-- Default Role Hint", 1
    )[0]


def _empty_state() -> str:
    return _source().split("<!-- Empty -->", 1)[1].split("<!-- Role List -->", 1)[0]


def _desktop_actions() -> str:
    return _source().split(
        '<td class="px-5 py-4 whitespace-nowrap text-right text-sm font-medium">', 1
    )[1].split("</td>", 1)[0]


def _mobile_actions() -> str:
    return _source().split("pt-3 mt-3 border-t border-gray-100", 1)[1].split(
        "</div>\n        </div>", 1
    )[0]


def test_view_declares_permission_guards_from_backend_codes():
    """守卫必须来自 useUser().hasPermission，且与后端权限码一一对应。"""
    source = _source()

    assert "import { useUser } from '../composables/useUser'" in source
    assert "const { hasPermission } = useUser()" in source
    assert "const canEditRole = computed(() => hasPermission('element:role:edit'))" in source
    assert "const canDeleteRole = computed(() => hasPermission('element:role:delete'))" in source


@pytest.mark.parametrize("handler", ["openCreateDialog", "editRole", "openPermissionDialog", "openUserAssignmentDialog"])
def test_edit_level_entries_are_guarded_everywhere(handler: str):
    """创建/编辑/分配权限/分配用户：所有出现处（桌面+移动端+空态）都必须带守卫。"""
    tags = _buttons(handler)

    assert tags, f"`{handler}` 的按钮没找到，选择器可能失效"
    for tag in tags:
        assert 'v-if="canEditRole"' in tag or 'v-if="canEditRole && ' in tag, (
            f"`{handler}` 按钮缺少 canEditRole 守卫：{tag}"
        )


def test_desktop_action_cell_guards_each_button():
    """桌面操作列的每个按钮单独守卫，而不是整列一刀切。"""
    cell = _desktop_actions()

    assert cell.count('v-if="canEditRole"') >= 3, "编辑/分配权限/分配用户 三个按钮都要各自守卫"
    assert 'v-if="canDeleteRole && !isSystemRole(role)"' in cell, "删除按钮要同时排除系统角色"
    assert 'v-if="!canEditRole && !canDeleteRole"' in cell, "无操作权限时应显示占位符而不是空列"


def test_mobile_action_row_guards_each_button():
    """移动端卡片与桌面表格是对等实现，不能只改一处。"""
    row = _mobile_actions()

    assert row.count('v-if="canEditRole"') >= 3, "移动端三个编辑级按钮都要守卫"
    assert 'v-if="canDeleteRole && !isSystemRole(role)"' in row, "移动端删除按钮同样要排除系统角色"


def test_create_entry_points_are_guarded():
    """顶栏「创建角色」与空态「创建角色」都不能无条件渲染。"""
    header = _header()
    empty = _empty_state()

    assert 'v-if="canEditRole"' in header, "顶栏创建角色缺少守卫"
    assert 'v-if="canEditRole"' in empty, "空态创建角色缺少守卫"


def test_delete_is_not_offered_for_system_roles():
    """系统角色（default/admin/system）不提供删除入口，避免误删登录默认角色。"""
    source = _source()
    assert "return code === 'default' || code === 'admin' || code === 'system'" in source
    assert 'v-if="canDeleteRole && !isSystemRole(role)"' in source
