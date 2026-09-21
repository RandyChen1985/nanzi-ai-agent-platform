"""角色管理页 default 角色缺失提示与一键初始化的契约（源码级）。

背景：新增用户弹窗会自动勾选 `default` 业务角色，但若平台尚未创建该角色，
用户侧只是"勾不上"，管理员在角色管理页也得不到任何提示。这里锁定三点：

1. 角色列表页在缺失 `default` 角色时展示提示层，并提供「一键初始化 default 角色」按钮；
2. 缺失判定走关键词检索（`search` + `size: 1000`）后精确匹配 code/name，
   不能只看当前分页，否则翻页场景会误报；
3. 一键初始化复用现有 `POST /api/portal/roles` 创建空角色（仅 code/name/description，
   不附带任何权限），并在创建/删除/编辑角色后重新探测，保证提示层及时出现或消失。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLES_VUE = ROOT / "frontend/src/views/Roles.vue"

pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return ROLES_VUE.read_text(encoding="utf-8")


def _banner() -> str:
    """截取 default 角色缺失提示层模板片段。"""
    return _source().split("<!-- Default Role Hint", 1)[1].split("<!-- Loading -->", 1)[0]


def _checker() -> str:
    """截取 `checkDefaultRole` 函数体。"""
    return _source().split("const checkDefaultRole = async () => {", 1)[1].split(
        "const initializeDefaultRole", 1
    )[0]


def _initializer() -> str:
    """截取 `initializeDefaultRole` 函数体。"""
    return _source().split("const initializeDefaultRole = async () => {", 1)[1].split(
        "const resetFilters", 1
    )[0]


def test_banner_offers_one_click_initialization():
    """缺失 default 角色时展示提示层与一键初始化按钮。"""
    banner = _banner()

    assert 'v-if="defaultRoleMissing"' in banner
    assert "尚未初始化 default 角色" in banner
    assert "一键初始化 default 角色" in banner
    assert '@click="initializeDefaultRole"' in banner
    assert ':disabled="initializingDefaultRole"' in banner


def test_missing_detection_searches_beyond_current_page():
    """缺失判定必须全量检索 + 精确匹配，避免分页导致误报。"""
    checker = _checker()

    assert "search: DEFAULT_ROLE_KEY" in checker
    assert "size: 1000" in checker
    assert "items.some(matchesDefaultRole)" in checker
    assert "axios.get('/api/portal/roles'" in checker


def test_default_role_matches_code_or_name_case_insensitively():
    """`default` 需按 code 或 name 匹配，并忽略大小写与首尾空格。"""
    source = _source()

    assert "const DEFAULT_ROLE_KEY = 'default'" in source
    assert "String(role?.code ?? '').trim().toLowerCase()" in source
    assert "String(role?.name ?? '').trim().toLowerCase()" in source
    assert "code === DEFAULT_ROLE_KEY || name === DEFAULT_ROLE_KEY" in source


def test_initialization_creates_empty_role_through_existing_api():
    """一键初始化复用角色创建接口，只建空角色，不附带权限。"""
    initializer = _initializer()

    assert "axios.post('/api/portal/roles'" in initializer
    assert "code: DEFAULT_ROLE_KEY" in initializer
    assert "name: DEFAULT_ROLE_KEY" in initializer
    assert "permissions" not in initializer


def test_initialization_handles_already_exists_and_refreshes_list():
    """重复初始化（后端 400 已存在）按已存在处理，并刷新列表。"""
    initializer = _initializer()

    assert "status === 400" in initializer
    assert "defaultRoleExists.value = true" in initializer
    assert "showToast('default 角色已存在', 'warning')" in initializer
    assert "await fetchRoles()" in initializer


def test_probe_failure_does_not_raise_false_banner():
    """探测失败时按"存在"处理，避免误报提示层。"""
    source = _source()

    assert "const defaultRoleExists = ref(true)" in source
    assert "defaultRoleExists.value = true" in _checker()


def test_probe_runs_on_mount_and_after_role_mutations():
    """进入页面、创建/编辑、删除角色后都要重新探测。"""
    source = _source()
    mount = source.split("onMounted(() => {\n    fetchRoles()", 1)[1].split("})", 1)[0]
    save_role = source.split("const saveRole = async () => {", 1)[1].split(
        "const confirmDelete", 1
    )[0]
    delete_role = source.split("const deleteRole = async () => {", 1)[1].split(
        "const openPermissionDialog", 1
    )[0]

    assert "checkDefaultRole()" in mount
    assert "checkDefaultRole()" in save_role
    assert "checkDefaultRole()" in delete_role
