"""菜单管理页与外链容器页的契约。

两页都承担「会改数据」或「会加载第三方页面」的职责，因此这里只钉关键边界：
菜单管理不能绕过内置项的身份字段、外链容器必须限制协议与沙箱、失败要有逃生路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

MANAGEMENT = Path("frontend/src/views/MenuManagement.vue")
EXTERNAL = Path("frontend/src/views/ExternalMenuView.vue")
SIDEBAR_LINK = Path("frontend/src/components/menu/SidebarMenuLink.vue")
pytestmark = pytest.mark.no_infrastructure


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_menu_management_talks_to_the_menu_api_only():
    source = _read(MANAGEMENT)

    for endpoint in (
        "axios.get('/api/portal/menus')",
        "axios.post('/api/portal/menus'",
        "axios.put(`/api/portal/menus/${",
        "axios.delete(`/api/portal/menus/${",
        "axios.post('/api/portal/menus/reorder'",
        "axios.post('/api/portal/menus/reset'",
    ):
        assert endpoint in source, f"缺少接口调用：{endpoint}"


def test_builtin_identity_fields_are_read_only_in_the_form():
    """内置页面的路由与权限码只展示不可改：改了会与代码里的路由/校验脱钩。"""
    source = _read(MANAGEMENT)

    assert "内置项：允许改展示属性，禁止改身份字段" in source
    assert ":value=\"draft.path || ''\"" in source
    assert "draft.permission_code || '（公开，所有登录用户可见）'" in source
    # 只读字段统一走 disabled，避免"看起来能改、保存无效"
    assert ':disabled="readonlyFields.label"' in source


def test_elements_are_display_only():
    source = _read(MANAGEMENT)

    assert "draft.node_type === 'element'" in source
    assert "require_permission" in source
    assert "不能在菜单管理里增删或改名" in source


def test_new_nodes_are_limited_to_groups_and_external_links():
    """不允许凭空造页面：前端没有对应路由，点开必然白屏。"""
    source = _read(MANAGEMENT)

    assert "startCreate('group')" in source
    assert "startCreate('external')" in source
    assert "只允许新增分组或外链菜单" in _read(Path("app/services/menu_service.py"))


def test_reset_and_delete_require_confirmation():
    source = _read(MANAGEMENT)

    assert 'v-if="showResetConfirm"' in source
    assert 'v-if="showDeleteConfirm"' in source
    assert "恢复默认" in source
    assert "此操作不可撤销" in source


def test_saving_refreshes_the_sidebar_immediately():
    source = _read(MANAGEMENT)

    assert "refreshNavigation" in source
    # 保存/删除/排序/恢复之后都要刷新，否则要手动刷新页面才能看到变化
    assert source.count("await refreshNavigation()") >= 4


def test_table_missing_shows_migration_hint_instead_of_breaking():
    source = _read(MANAGEMENT)

    assert "table_ready" in source
    assert "V176-create_agent_menus.sql" in source
    assert "V77-create_agent_menus.sql" in source


def test_external_view_never_renders_an_unknown_url():
    """导航树里查不到（或类型不是 external）就不渲染 iframe——等同于无权限。"""
    source = _read(EXTERNAL)

    assert "findNode(code.value)" in source
    assert "found.node_type === 'external'" in source
    assert 'v-if="!node"' in source


def test_external_view_sandboxes_the_frame_and_explains_rejection():
    source = _read(EXTERNAL)

    assert "sandbox=" in source
    assert "allow-scripts" in source
    assert "X-Frame-Options" in source
    assert "CSP frame-ancestors" in source
    assert "新标签" in source


def test_external_view_has_a_load_timeout_fallback():
    source = _read(EXTERNAL)

    assert "LOAD_TIMEOUT_MS" in source
    assert "setTimeout" in source
    assert "clearTimeout" in source


def test_sidebar_link_distinguishes_new_tab_and_embed():
    source = _read(SIDEBAR_LINK)

    assert 'target="_blank"' in source
    assert "rel=\"noopener noreferrer\"" in source
    assert "/dashboard/external/" in source
    assert "node.open_mode === 'embed'" in source


def test_tree_supports_right_click_shortcuts():
    """右键菜单是这套页面的主要操作路径之一：树上的快捷动作必须与属性面板同源。"""
    source = _read(MANAGEMENT)

    # 容器与节点都要绑定，且节点上阻止冒泡（否则会同时弹出两个菜单）
    assert '@contextmenu="onNodeContextMenu($event, null)"' in source
    assert '@contextmenu="onNodeContextMenu($event, group)"' in source
    assert '@contextmenu="onNodeContextMenu($event, item)"' in source
    # 只读用户不该被"吃掉"右键：交给浏览器默认菜单
    assert "if (!canEdit.value) return" in source

    for label in ('新建分组', '设为默认落地页', '停用', '启用', '删除外链菜单'):
        assert label in source, f"右键菜单缺少动作：{label}"


def test_right_click_menu_closes_on_outside_click_but_not_on_its_own_items():
    """`mousedown` 早于 `click`：不排除菜单自身的话，点菜单项会先清空状态、动作静默失效。"""
    source = _read(MANAGEMENT)

    assert 'ref="contextMenuEl"' in source
    assert "contextMenuEl.value?.contains(event.target as Node)" in source
    assert "document.addEventListener('mousedown', onDocumentPointerDown, true)" in source
    assert "document.addEventListener('keydown', onGlobalKeydown)" in source


def test_shortcut_actions_reuse_the_same_endpoint_as_the_panel():
    source = _read(MANAGEMENT)

    assert "async function patchNode" in source
    assert "await axios.put(`/api/portal/menus/${node.id}`, patch)" in source
    # 快捷操作改完也要刷新侧栏，否则要手动刷新页面才看到
    assert source.count("await refreshNavigation()") >= 5


def test_new_external_defaults_to_the_currently_selected_group():
    """在某个分组（或它的子项）上点新建，默认就加到那里，省一次选择。"""
    source = _read(MANAGEMENT)

    assert "function defaultParentCode(): string | null" in source
    assert "if (current.node_type === 'group') return current.code" in source
    assert "if (current.parent_code) return current.parent_code" in source
    # 必须先算默认值再清空 selectedCode，否则永远只能拿到第一个分组
    start_create = source[source.index("function startCreate("):]
    start_create = start_create[: start_create.index("\n}\n")]
    assert start_create.index("const resolvedParent") < start_create.index("selectedCode.value = ''")


def test_icon_field_renders_a_preview_picker_instead_of_a_plain_select():
    """只列 key（`chat_bubble_left_right`）没法判断长什么样，必须给图标 + 中文名。"""
    source = _read(MANAGEMENT)
    picker = Path("frontend/src/components/menu/MenuIconPicker.vue").read_text(encoding="utf-8")

    assert "<MenuIconPicker" in source
    assert 'v-model="draft.icon"' in source
    # 图标字段不再用原生 select
    assert "iconKeys" not in source

    assert "<MenuIcon :name=\"key\"" in picker
    assert "MENU_ICON_LABELS" in picker
    assert "不设置图标" in picker
    # 同样要能点外部/Esc 收起
    assert "document.addEventListener('mousedown', onDocumentPointerDown, true)" in picker
    assert "event.key === 'Escape'" in picker


def test_icon_picker_panel_is_scrollable_and_never_collapses_while_scrolling_it():
    """面板有 23 个图标，必须能滚动看全；且滚它自己不能把面板收起。

    第一版把面板放在表单里，`main` 的 `overflow-y-auto` 会把下半截裁掉；
    同时捕获阶段的 scroll 监听把"滚面板"也当成"页面滚动"直接关闭，
    表现就是滚轮一动面板就消失、后面的图标永远选不到。
    """
    picker = Path("frontend/src/components/menu/MenuIconPicker.vue").read_text(encoding="utf-8")

    # Teleport 到 body：脱离属性面板的滚动容器
    assert '<Teleport to="body">' in picker
    assert "fixed z-[70]" in picker
    # 面板自身可滚动，且滚动不外溢到页面
    assert "overflow-y-auto" in picker
    assert "overscroll-contain" in picker
    # 高度按按钮上下空间现算：下方不够就向上弹
    assert "maxHeight" in picker and "spaceBelow" in picker and "openUp" in picker
    # 面板内滚动 / 面板内点击都不关闭（捕获阶段监听必须自己排除面板）
    assert "panelEl.value?.contains(target)" in picker
    assert "if (target instanceof Node && panelEl.value?.contains(target)) return" in picker


def test_self_created_empty_group_can_be_deleted_from_both_entry_points():
    """误建的分组要能清掉：属性面板与右键菜单都必须给出口，且有子项时不给。"""
    source = _read(MANAGEMENT)

    assert "function hasChildren(code: string): boolean" in source
    assert "node.node_type === 'group' && !hasChildren(node.code)" in source
    assert "v-if=\"canDeleteDraft\"" in source
    assert "删除空分组" in source
    assert "分组下还有菜单，先移走子项再删" in source
    # 删除确认文案按节点类型区分
    assert "deleteTitle" in source and "deleteMessage" in source
    assert "这是一个自建的空分组" in source


def test_embedded_external_page_fills_the_content_area():
    """内嵌页要铺满：外面再套一层 gutter，第三方界面会被挤成"卡片里的网页"。

    `Dashboard` 的内容区默认带 `px-8 pt-3.5 pb-6`（桌面端），外链页必须走 `p-0`，
    与聊天页、个人中心同一口径；否则窄屏下两侧会白吃掉约 64px 宽度。
    """
    dashboard = _read(Path("frontend/src/views/Dashboard.vue"))
    view = _read(EXTERNAL)

    assert 'if (route.name === "ExternalMenu") return "p-0";' in dashboard
    # iframe 自身满宽满高，不依赖父级 padding 撑开
    assert 'class="h-full w-full border-0"' in view
    assert 'class="flex h-full min-h-0 flex-col"' in view


def test_write_entry_points_are_hidden_when_the_user_cannot_edit():
    """无 element:menu:edit 时应是「整页只读」，而不是一排灰按钮。

    灰按钮会让人以为"点一下就能用"，而实际是权限不足；项目里角色管理、数据源、
    MCP 入口都是这个口径：无权限就不渲染入口。
    """
    source = _read(MANAGEMENT)

    # 首屏即按 element 码判定，不依赖"点了才 403"
    assert "import { useUser }" in source
    assert "hasPermission('element:menu:edit')" in source
    assert "const editDenied = ref(false)" in source
    # 本地权限快照可能过期，后端 403 时也要降级为只读
    assert "if (error?.response?.status === 403) editDenied.value = true" in source

    # 三组入口（工具栏 / 排序 / 保存删除）都在 canEdit 之下
    assert source.count('v-if="canEdit"') >= 4
    # 不再用置灰代替隐藏
    assert ':disabled="!canEdit' not in source
    # 只读时字段全部锁死：否则能改、却存不下去
    readonly_block = source[source.index("const readonlyFields"):]
    readonly_block = readonly_block[: readonly_block.index("async function loadNodes")]
    assert "if (!canEdit.value) {" in readonly_block
    for field in ("label", "icon", "url", "open_mode", "slug", "sort_order", "is_landing"):
        assert f"{field}: true" in readonly_block

    # 只读态要有明确说明，别让人以为页面坏了
    assert "只读模式 · 无菜单编辑权限" in source
    assert "只读模式：可查看但不能修改" in source
