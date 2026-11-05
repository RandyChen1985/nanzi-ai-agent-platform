"""用户模式侧栏的静态契约。

用户模式是「写死的 4 个入口 + 更多」，和动态菜单（/menus/nav）是两条独立路径。
这里把几条容易被后续改动破坏的边界钉住：

1. 定时任务走 /dashboard/my-tasks（只列自己的任务），不能偷偷挂回 menu:task_center，
   否则普通用户点进去会被守卫弹回；
2. 资料库是全站唯一「无权限也保留位置、置灰 + 提示」的入口，其余默认隐藏；
3. 系统管理只给管理员；
4. 模式偏好必须走专用 /ui-mode 端点——全量 PUT 会覆盖掉用户的置顶等偏好。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def menu() -> str:
    return _read("frontend/src/constants/userModeMenu.ts")


@pytest.fixture(scope="module")
def sidebar() -> str:
    return _read("frontend/src/components/layout/UserModeSidebar.vue")


@pytest.fixture(scope="module")
def dashboard() -> str:
    return _read("frontend/src/views/Dashboard.vue")


@pytest.fixture(scope="module")
def router_source() -> str:
    return _read("frontend/src/router/index.ts")


@pytest.fixture(scope="module")
def ui_mode_composable() -> str:
    return _read("frontend/src/composables/useUiMode.ts")


def test_menu_keeps_only_four_primary_entries(menu: str) -> None:
    """一级入口固定为：新建会话 / 专家技能连接器 / 定时任务 / 知识库 / 工作空间 / 更多。"""
    for key in ('key: "new-chat"', 'key: "experts"', 'key: "tasks"', 'key: "library"', 'key: "workspace"', 'key: "more"'):
        assert key in menu
    # 助理入口已废弃，不能加回来
    assert 'label: "助理"' not in menu


def test_new_chat_entry_opens_blank_conversation(menu: str) -> None:
    assert 'kind: "new-chat"' in menu
    assert 'path: "/dashboard/chat"' in menu


def test_experts_group_points_at_existing_routes(menu: str) -> None:
    assert 'path: "/dashboard/agent-management"' in menu
    assert 'path: "/dashboard/skills"' in menu
    # 连接器面向用户，落 MCP 工具集页；MCP 服务台留给专业模式
    assert 'path: "/dashboard/mcp"' in menu
    assert 'path: "/dashboard/mcp-service"' not in menu


def test_tasks_entry_does_not_require_task_center_permission(menu: str, router_source: str) -> None:
    """定时任务 = 自己的任务，人人可用；不能挂 menu:task_center。"""
    tasks_block = menu.split('key: "tasks"', 1)[1].split("},", 1)[0]
    assert 'path: "/dashboard/my-tasks"' in tasks_block
    assert "permission" not in tasks_block

    my_tasks_route = router_source.split("name: 'MyTasks'", 1)[1].split("}", 1)[0]
    assert "perm" not in my_tasks_route


def test_library_is_the_only_locked_entry(menu: str) -> None:
    """知识库没权限时置灰而不是消失；其余入口默认隐藏。"""
    assert menu.count('onDenied: "lock"') == 1
    library_block = menu.split('key: "library"', 1)[1].split("},", 1)[0]
    assert 'onDenied: "lock"' in library_block
    assert 'permission: "menu:knowledge_management"' in library_block


def test_system_menu_is_removed(menu: str) -> None:
    """「系统管理」已从用户模式移除：管理员要看后台就切专业模式。"""
    assert 'key: "system"' not in menu


def test_sidebar_renders_locked_hint_instead_of_navigating(sidebar: str) -> None:
    """置灰项点击只提示，不能跳转（否则会撞上路由守卫被弹回）。"""
    assert "当前账号没有「" in sidebar
    assert 'if (item.locked) {' in sidebar
    assert "emit(\"toast\"" in sidebar


def test_sidebar_lists_conversations_with_total_count(sidebar: str) -> None:
    assert "/api/v1/chat/history" in sidebar
    assert "USER_MODE_TASK_PAGE_SIZE" in sidebar
    assert "会话列表（{{ taskTotal }}）" in sidebar


def test_dashboard_switches_sidebar_by_mode(dashboard: str) -> None:
    assert "UserModeSidebar" in dashboard
    assert "isUserMode" in dashboard
    # 用户模式展开 252、收起 68；专业模式维持原来的折叠宽度
    assert 'if (isUserMode.value) return isCollapsed.value ? "w-[68px]" : "w-[252px]"' in dashboard
    # 专业模式仍然渲染动态菜单
    assert "visibleNavNodes" in dashboard


def test_new_chat_remounts_chat_page_when_already_there(dashboard: str) -> None:
    """已经在聊天页时再点新建会话，必须换 key 重挂载，否则看起来「点了没反应」。"""
    assert "pageReloadToken.value += 1" in dashboard
    assert 'route.name === "AIChat"' in dashboard


def test_ui_mode_is_persisted_through_dedicated_endpoint(ui_mode_composable: str) -> None:
    """切模式单独打 /ui-mode，不能走全量 PUT（会清掉置顶等偏好）。"""
    assert "/api/portal/portal-prefs/ui-mode" in ui_mode_composable
    assert 'ui_mode: mode' in ui_mode_composable


def test_ui_mode_default_follows_role(ui_mode_composable: str) -> None:
    assert 'role.trim().toLowerCase() === "admin" ? "pro" : "user"' in ui_mode_composable


def test_ui_mode_cache_is_scoped_per_user(ui_mode_composable: str) -> None:
    """换号后不能串用上一个账号的模式（和菜单快照同一个坑）。"""
    assert "nanzi_ui_mode:" in ui_mode_composable or "`${CACHE_PREFIX}:${userId" in ui_mode_composable


def test_personal_center_exposes_mode_switch() -> None:
    """入口在左侧子菜单「界面偏好」（方案 B），不再占用 tab 行右侧。"""
    personal = _read("frontend/src/views/PersonalCenter.vue")
    assert "handleSwitchUiMode" in personal
    assert "界面偏好" in personal
    assert 'data-user-sub-tab="preferences"' in personal
    assert 'data-user-panel="preferences"' in personal
    # 方案 A 的分段控件已移除
    assert "items-end justify-between" not in personal

def test_permission_filtering_runs_correctly() -> None:
    """真跑一遍权限过滤（esbuild 打包后在 Node 里执行），而不只是看源码字符串。"""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过真跑校验")

    probe = ROOT / "tests/frontend/user_mode_menu_probe.cjs"
    result = subprocess.run(
        [node, str(probe)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    # 零权限账号：定时任务仍然可见，资料库保留但置灰
    assert payload["tasksAlwaysVisible"] is True
    assert payload["noKbLibraryLocked"] is True
    # 管理员才有系统管理；普通用户给满菜单码也不该出现
    assert payload["adminHasSystem"] is True
    assert payload["userHasSystem"] is False
    # 专家组在用户模式下不挂权限：零权限账号也能看到三个子项
    assert payload["hasExpertsGroup"] is True
    assert payload["partialExperts"] == ["expert", "skill", "connector"]


def test_new_task_opens_a_blank_conversation_not_the_last_one() -> None:
    """「新建会话」必须跳过会话续接，开一条空白会话。

    只重挂载 iframe 是不够的：挂件会按会话指针续接上一次对话。
    `new=1` 由 Dashboard → Chat.vue → iframe URL 一路透传，挂件据此生成新 cid。
    """
    dashboard = _read("frontend/src/views/Dashboard.vue")
    chat = _read("frontend/src/views/Chat.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert 'new: "1"' in dashboard
    assert "embedQuery.set('new', '1')" in chat
    assert 'query.get("new") === "1"' in embed
    # 与 preview 的临时会话区分：这里要正常落盘
    assert "isForceNewSession" in embed
    assert "generateNewConversation();" in embed


def test_user_mode_lands_on_new_task_after_login() -> None:
    """用户模式登录直接落在「新建会话」，不绕概览/工作台。"""
    login = _read("frontend/src/views/Login.vue")
    router_source = _read("frontend/src/router/index.ts")

    assert "resolveInitialUiMode" in login
    assert "query: { new: '1' }" in login
    # 根路径与守卫兜底同样按模式分流
    assert "getCachedUiMode() === 'user' ? '/dashboard/chat' : '/dashboard'" in router_source
    assert "if (getCachedUiMode() === 'user') return '/dashboard/chat'" in router_source


def test_user_mode_hides_the_top_header_and_moves_actions_to_sidebar() -> None:
    """用户模式不再有右侧顶栏，通知 / 主题 / 退出收进左下角用户卡。"""
    dashboard = _read("frontend/src/views/Dashboard.vue")
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    # 桌面隐藏、移动端保留（否则没有抽屉入口）
    assert 'v-if="showDashboardHeader && (!isUserMode || isMobile)"' in dashboard
    assert '<div v-if="!isUserMode" class="flex items-center space-x-2' in dashboard
    # 三个动作在侧栏底部
    assert 'panel-placement="up"' in sidebar
    assert "emit('toggle-theme')" in sidebar
    assert "emit('logout')" in sidebar
    assert "PortalNotificationBell" in sidebar


def test_notification_panel_can_open_upwards() -> None:
    """侧栏底部空间有限，通知面板必须能向上展开，否则被视口底部切掉。"""
    bell = _read("frontend/src/components/PortalNotificationBell.vue")

    assert "panelPlacement" in bell
    assert "sm:bottom-full" in bell
    assert "sm:mb-2" in bell


def test_experts_group_opens_first_child_on_click() -> None:
    """点「专家·技能·连接器」应直接进专家，而不是只展开、再让用户点一次子项。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")
    menu = _read("frontend/src/constants/userModeMenu.ts")

    assert "expandOpensFirst: true" in menu
    assert "item.expandOpensFirst && !isChildActive(item)" in sidebar
    assert 'emit("navigate", first.path)' in sidebar


def test_user_mode_defaults_to_light_theme() -> None:
    """进入用户模式默认亮色。

    只覆盖「进入」这一次：用户之后在用户模式里手动切深色，刷新不会被拽回亮色，
    所以不能在 ensureUiMode 每次加载时都强制 setTheme。
    """
    ui_mode = _read("frontend/src/composables/useUiMode.ts")
    login = _read("frontend/src/views/Login.vue")

    assert "applyUserModeDefaultTheme" in ui_mode
    assert 'if (mode === "user") applyUserModeDefaultTheme()' in ui_mode
    assert "applyUserModeDefaultTheme()" in login


def test_knowledge_and_workspace_are_siblings(menu: str) -> None:
    """知识库与工作空间是平级入口；工作空间读自己的目录，不挂权限。"""
    library_block = menu.split('key: "library"', 1)[1].split('key: "workspace"', 1)[0]
    assert 'label: "知识库"' in library_block
    assert "资料库" not in menu

    workspace_block = menu.split('key: "workspace"', 1)[1].split("},", 1)[0]
    assert 'path: "/dashboard/workspace"' in workspace_block
    assert "permission" not in workspace_block


def test_more_menu_drops_memory_and_system_adds_overview(menu: str) -> None:
    more_block = menu.split('key: "more"', 1)[1]
    assert 'key: "overview"' in more_block
    assert 'path: "/dashboard"' in more_block
    assert 'key: "memory"' not in more_block
    assert 'key: "system"' not in more_block
    # 个人中心入口收敛到头像弹层（AccountMenu），侧栏不重复第二份
    assert 'key: "personal"' not in more_block


def test_workspace_page_hides_session_mount_action() -> None:
    """独立页不提供「挂载到会话」：那需要目标会话，只属于聊天抽屉。

    页面本身可以（也应该）在注释里解释为什么没有它，所以断言落在**渲染层**：
    行操作组件必须显式过滤掉 `mount` 这一项。
    """
    actions = _read("frontend/src/components/workspace/WorkspaceRowActions.vue")
    assert 'entry.key !== "mount"' in actions
    # 抽屉里的挂载能力保持原样，不能被这次改动删掉
    drawer = _read("frontend/src/components/embed/WorkspaceBrowserDrawer.vue")
    assert "挂载" in drawer


def test_workspace_route_has_no_permission_gate() -> None:
    router_source = _read("frontend/src/router/index.ts")
    route_block = router_source.split("name: 'WorkspaceManage'", 1)[1].split("}", 1)[0]
    assert "perm" not in route_block


def test_force_new_session_is_one_shot_and_yields_to_host_conversation() -> None:
    """`new=1` 只能生效一次。

    它在 iframe URL 上会一直留在挂件实例里。若不清掉，此后宿主每次下发会话 ID
    （点历史会话）都会被它抹掉，表现为「点哪条历史都停在新会话」。
    """
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert "isForceNewSession = false;" in embed
    # 宿主下发会话时让位
    assert "if (!requestedConversationId) {" in embed
    # 采用分支里也把一次性指令作废
    assert "// 宿主明确要某个会话时，「新建」这条一次性指令直接作废" in embed


def test_notification_panel_up_mode_overrides_top() -> None:
    """向上展开时必须把 top 归零。

    面板基类带 `top-[3.25rem]`；只加 `sm:bottom-full` 会让 absolute 元素同时有 top 和
    bottom，高度被拉到「按钮顶部到 3.25rem」之间，视觉上就是点了打不开。
    """
    bell = _read("frontend/src/components/PortalNotificationBell.vue")
    assert "sm:top-auto sm:bottom-full sm:left-0 sm:right-auto sm:mb-2" in bell


def test_workspace_page_matches_drawer_capabilities() -> None:
    """独立页要与聊天抽屉的能力对齐（除挂载/本会话目录/画布预览）。"""
    page = _read("frontend/src/views/WorkspaceManage.vue")

    # 新建文件 + 新建文件夹
    assert 'kind: dialog.value === "dir" ? "dir" : "file"' in page
    # 解压
    assert "extractWorkspaceArchive" in page
    assert "isExtractableArchive" in page
    # 复制路径
    assert "copyTextToClipboard" in page
    # 清空回收站
    assert "emptyWorkspaceTrash" in page
    # 拖拽上传
    assert '@drop.prevent="onDrop"' in page
    # 批量选择 + 批量删除
    assert "toggleSelectAll" in page
    assert "askBatchDelete" in page
    # 全目录搜索
    assert "/api/v1/chat/fs/search" in page
    # 类型筛选 / 排序 / 视图切换
    assert "typeFilter" in page and "sortBy" in page and "viewMode" in page
    # 最近文件
    assert "/api/v1/chat/fs/recent-files" in page


def test_overview_item_does_not_prefix_match_every_dashboard_route() -> None:
    """「系统概览」指向 /dashboard 本身，必须精确匹配。

    按前缀匹配的话，停在 /dashboard/token-stats 时它也会点亮，
    侧栏会同时出现「Token 消耗 + 系统概览」两条高亮。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")
    assert 'if (target === "/dashboard" || target === "/dashboard/") return current === "/dashboard"' in sidebar


def test_notification_panel_opens_to_the_right_inside_sidebar() -> None:
    """侧栏底部的通知面板要向右展开：向左会被视口左边裁掉。"""
    bell = _read("frontend/src/components/PortalNotificationBell.vue")
    assert "sm:left-0 sm:right-auto" in bell


def test_workspace_page_uses_shared_hero_silhouette() -> None:
    """工作空间页沿用知识库的 hero 配方（同一套剪影组件），不重画一份。"""
    page = _read("frontend/src/views/WorkspaceManage.vue")
    assert "HeroSilhouette" in page
    assert 'variant="cards"' in page
    assert "hero-surface" in page


def test_workspace_preview_reuses_chat_canvas() -> None:
    """预览必须复用聊天页的画布，不能退化成「新窗口打开原始响应」。

    `/fs/preview` 返回的是原始响应：文本变成纯文本、Office 直接触发下载，
    只有画布才知道怎么渲染 markdown / 代码 / CSV / 文档。
    """
    page = _read("frontend/src/views/WorkspaceManage.vue")

    assert "ChatCanvas" in page
    assert "buildWorkspaceCanvasPayload" in page
    # 不能再走裸 window.open
    assert 'window.open(url, "_blank", "noopener")' not in page
    # 必须经 useWorkspaceCanvas 解析 canvas:// 协议后再交给画布，
    # 否则画布拿到的只是一串 `canvas://file?path=...`，什么都渲染不出来
    assert "useWorkspaceCanvas" in page
    assert "void handleOpenCanvas(buildWorkspaceCanvasPayload(" in page
    assert ':data="canvasData"' in page
    assert ':visible="canvasVisible"' in page


def test_only_the_active_group_stays_expanded() -> None:
    """路由变化时，其他分组要收起。

    之前只写 `expanded[key] = true`、从不置回 false：点开过「更多」之后再点定时任务，
    那一组会一直挂在下面，看起来像「点别的菜单把更多也展开了」。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "const containsCurrent = item.children.some((child) => pathMatches(path, child.path))" in sidebar
    assert "expanded[item.key] = containsCurrent" in sidebar
    # 「更多」里挂着指向 /dashboard 的「系统概览」：分组判断若按前缀匹配，
    # 任意 /dashboard/* 都算命中，「更多」就永远展开。
    assert "function pathMatches(current: string, target?: string): boolean" in sidebar
    assert 'if (target === "/dashboard" || target === "/dashboard/") return current === "/dashboard"' in sidebar


def test_professional_mode_defaults_to_dark_theme() -> None:
    """切到专业模式要回到深色。

    这是平台原本的视觉；用户模式的亮色是简化布局的配套。
    两条路（切换 + 登录落地）都要覆盖，否则从用户模式切过去会停在亮色。
    每个模式还会记住自己上一次的主题：用户模式里手动切过深色，切回来仍是深色。
    """
    ui_mode = _read("frontend/src/composables/useUiMode.ts")
    login = _read("frontend/src/views/Login.vue")

    assert "export function applyProModeDefaultTheme" in ui_mode
    assert 'return readModeTheme(mode) ?? (mode === "user" ? "light" : "dark")' in ui_mode
    assert "else applyProModeDefaultTheme()" in ui_mode
    assert "applyProModeDefaultTheme()" in login


def test_user_mode_sidebar_can_collapse() -> None:
    """用户模式的侧栏要能真的收起，而不是只有原型里的一个箭头。

    收起后：宽度 252→68、只留图标、任务列表与用户卡文字隐藏；
    分组没有空间展开，点一下直接进第一个子项。
    """
    dashboard = _read("frontend/src/views/Dashboard.vue")
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert 'if (isUserMode.value) return isCollapsed.value ? "w-[68px]" : "w-[252px]"' in dashboard
    assert ':collapsed="isCollapsed && !isMobile"' in dashboard
    assert '@toggle-collapse="toggleSidebar"' in dashboard

    assert "emit('toggle-collapse')" in sidebar
    assert "if (props.collapsed) {" in sidebar
    assert '<span v-if="!collapsed" class="min-w-0 flex-1 truncate">{{ item.label }}</span>' in sidebar
    assert 'v-show="!collapsed && expanded[item.key] !== false"' in sidebar


def test_collapsed_sidebar_keeps_a_way_back() -> None:
    """收起后必须有展开入口。

    折叠按钮自己在 `v-if="!collapsed"` 里，收起来就一起消失了；
    没有别的入口（用户模式桌面把顶栏的折叠按钮也藏了），等于只能刷新页面才能回来。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert 'title="展开侧栏"' in sidebar
    assert 'aria-label="展开侧栏"' in sidebar


def test_task_list_pages_in_eight_and_loads_on_scroll() -> None:
    """任务列表首屏 8 条，滚到底部自动补一页；不再跳去聊天页看全部。"""
    menu = _read("frontend/src/constants/userModeMenu.ts")
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "USER_MODE_TASK_PAGE_SIZE = 8" in menu
    assert "const loadMoreTasks = async () =>" in sidebar
    assert '@scroll.passive="onScroll"' in sidebar
    assert "el.scrollTop + el.clientHeight >= el.scrollHeight - 48" in sidebar
    # 「查看全部会话」跳转已移除
    assert "查看全部会话" not in sidebar
    assert "已加载全部会话" in sidebar


def test_workspace_rows_open_on_double_click() -> None:
    """双击行/卡片打开：目录进去、文件开预览。

    列表视图里文件名对文件是不可点的，「⋯」菜单又要点两层，
    双击是最直接的「打开」手势。
    """
    page = _read("frontend/src/views/WorkspaceManage.vue")

    assert 'const openItem = (item: FsItem) => {' in page
    assert '@dblclick="openItem(item)"' in page
    assert '@dblclick.stop="openItem(item)"' in page
    assert "if (item.is_dir) {" in page


def test_switching_to_user_mode_falls_back_when_page_is_unreachable() -> None:
    """切到用户模式时，若当前页不在用户菜单里，回落到新建会话页。

    典型场景：在专业模式的「用户管理」页切模式，用户模式根本没有那个入口，
    停在原页面会让侧栏里「找不到自己」。
    """
    menu = _read("frontend/src/constants/userModeMenu.ts")
    dashboard = _read("frontend/src/views/Dashboard.vue")

    assert "export function collectUserModePaths(" in menu
    assert "collectUserModePaths()" in dashboard
    assert "if (!stillReachable) router.replace(\"/dashboard/chat\");" in dashboard


def test_task_list_has_keyword_search() -> None:
    """任务列表要能搜索。

    滚动加载解决的是"翻得到"，解决不了"找得到"——839 条会话靠滑是不现实的。
    搜索复用 /chat/history 的 keyword 参数，与分页、滚动加载共用同一条加载链路。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "const taskSearchOpen = ref(false)" in sidebar
    assert "const taskKeyword = ref" in sidebar
    assert "if (keyword) params.keyword = keyword" in sidebar
    assert "没有匹配的会话" in sidebar


def test_workspace_page_is_left_aligned() -> None:
    """工作空间内容靠左，不居中。

    居中（mx-auto）在侧栏收起成 68px 后会把富余宽度全堆到左侧，
    表现为"折叠之后多出一大块空白"。
    """
    page = _read("frontend/src/views/WorkspaceManage.vue")

    assert 'class="relative flex h-full w-full max-w-[1200px] flex-col' in page
    assert "relative mx-auto flex h-full w-full max-w-[1200px]" not in page


def test_experts_group_is_open_in_user_mode_only() -> None:
    """专家/技能/连接器在用户模式放开，专业模式仍按 menu:* 控制。

    三者的路由 meta.perm 保留（专业模式用），另加 `userModeAllowed`；
    守卫只在「用户模式 + 该标记」时跳过权限检查。
    """
    menu = _read("frontend/src/constants/userModeMenu.ts")
    router_source = _read("frontend/src/router/index.ts")

    experts_block = menu.split('key: "experts"', 1)[1].split('key: "tasks"', 1)[0]
    assert "permission" not in experts_block

    assert "userModeAllowed: true" in router_source
    assert router_source.count("userModeAllowed: true") == 3
    assert "const userModePass = Boolean(to.meta.userModeAllowed) && resolveInitialUiMode() === 'user'" in router_source
    # 专业模式的口径没有变：perm 还在
    assert "perm: 'menu:agent_management', userModeAllowed: true" in router_source
    assert "perm: 'menu:skills_management', userModeAllowed: true" in router_source
    assert "perm: 'menu:mcp_management', userModeAllowed: true" in router_source


def test_submenu_items_carry_icons() -> None:
    """子菜单项要有图标。

    缩进从 41px 收到 30px 给图标留位，图标 15px；没有图标时这一级只剩缩进，
    在长列表里很难一眼分辨「这是专家还是技能」。
    """
    menu = _read("frontend/src/constants/userModeMenu.ts")
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    # 三个专家子项 + 更多里的三项
    for key in ('"expert"', '"skill"', '"connector"', '"workbench"', '"token"', '"overview"'):
        segment = menu.split(f"key: {key}", 1)[1].split("}", 1)[0]
        assert "icon:" in segment, f"{key} 缺少图标"

    assert 'v-if="child.icon"' in sidebar
    assert 'class="h-[15px] w-[15px] flex-shrink-0 opacity-85"' in sidebar
    assert "pb-1 pl-[30px]" in sidebar


# ---------------------------------------------------------------------------
# 第二轮打磨：会话同步、置顶/分享/删除、失败态、竞态、分组与主题记忆
# ---------------------------------------------------------------------------


def test_locked_entry_stays_clickable_for_hint() -> None:
    """置灰入口不能用 `disabled`。

    disabled 的 button 在浏览器里不派发 click，`onItemClick` 里的 toast 分支
    就永远不可达——「点击提示联系管理员」只是写了代码，用户点了没反应。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert ':disabled="item.locked"' not in sidebar
    assert ':disabled="child.locked"' not in sidebar
    assert ':aria-disabled="item.locked ? \'true\' : undefined"' in sidebar
    assert 'emit("toast", lockedHint(item.label), "warning")' in sidebar


def test_task_list_survives_load_failure() -> None:
    """接口失败不能伪装成「还没有会话」。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "const loadError = ref(false)" in sidebar
    assert "loadError.value = true" in sidebar
    assert "会话列表加载失败。" in sidebar
    assert "点此重试" in sidebar
    # 失败时保留旧列表，不能清空
    failed_branch = sidebar.split("} catch {", 2)[1]
    assert "tasks.value = []" not in failed_branch


def test_task_requests_are_serialized() -> None:
    """搜索 / 分页要有请求序号：慢响应不能覆盖新关键词的结果。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "let loadSerial = 0" in sidebar
    assert "const serial = ++loadSerial" in sidebar
    assert "if (serial !== loadSerial) return" in sidebar


def test_task_list_is_grouped_and_uses_relative_time() -> None:
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert '["today", "今天"]' in sidebar
    assert '["yesterday", "昨天"]' in sidebar
    assert '["earlier", "更早"]' in sidebar
    assert "const formatTaskTime = (value?: string): string =>" in sidebar
    assert 'return "昨天"' in sidebar
    assert "getFullYear() === now.getFullYear()" in sidebar


def test_task_row_offers_pin_share_delete() -> None:
    """任务行要能管理会话：置顶 / 分享 / 删除（重命名暂无接口）。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "usePinnedConversations({ client: axios })" in sidebar
    assert "ShareConversationDialog" in sidebar
    assert "ConfirmModal" in sidebar
    assert "/api/v1/chat/history/batch-delete" in sidebar
    assert "取消置顶" in sidebar
    assert "分享" in sidebar
    assert "删除" in sidebar


def test_pinned_sessions_are_loaded_and_merged() -> None:
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "pinnedApi.load()" in sidebar
    assert 'key: "pinned"' in sidebar
    assert 'title: "置顶"' in sidebar
    # 置顶项优先用当前列表里的实时数据，列表没有时退回快照
    assert "entry.title || \"未命名对话\"" in sidebar


def test_menu_and_task_list_scroll_independently() -> None:
    """菜单固定、任务区独立滚动：任务多时菜单不能被一起滚走。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    menu_index = sidebar.index("max-h-[52vh] overflow-y-auto custom-scrollbar")
    task_index = sidebar.index('ref="scrollRef"')
    assert menu_index < task_index
    assert 'class="min-h-0 flex-1 overflow-y-auto px-2.5 pb-3 custom-scrollbar"' in sidebar


def test_new_chat_highlight_requires_no_active_conversation() -> None:
    """「新建会话」只在聊天页且没有打开会话时高亮。

    否则点开历史会话后它和当前任务同时亮，用户分不清自己在哪个会话里。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "const isNewChatActive = computed(" in sidebar
    assert '!String(props.activeConversationId || "")' in sidebar
    assert "isItemActive(item)" in sidebar


def test_collapsed_sidebar_keeps_actions() -> None:
    """收起成 68px 后仍要能看通知、切主题、退出登录。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    collapsed_block = sidebar.split('<template v-if="collapsed">', 1)[1].split("</template>", 1)[0]
    assert "PortalNotificationBell" in collapsed_block
    assert 'emit(\'toggle-theme\')' in collapsed_block
    assert 'emit(\'logout\')' in collapsed_block


def test_user_card_shows_role_instead_of_handle() -> None:
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "const roleLabel = computed(" in sidebar
    assert '"管理员" : "普通用户"' in sidebar
    assert "{{ roleLabel }}" in sidebar


def test_task_page_size_adapts_to_viewport() -> None:
    menu = _read("frontend/src/constants/userModeMenu.ts")
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "export function computeUserModeTaskPageSize" in menu
    assert "USER_MODE_TASK_PAGE_SIZE_MAX = 20" in menu
    assert "computeUserModeTaskPageSize(window.innerHeight)" in sidebar
    assert 'window.addEventListener("resize", handleResize)' in sidebar


def test_mobile_drawer_hides_collapse_button() -> None:
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert 'v-if="!mobile"' in sidebar
    assert "mobile?: boolean" in sidebar


def test_empty_state_offers_new_chat_button() -> None:
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert 'v-if="taskKeyword.trim()">没有匹配的会话。' in sidebar
    assert ">新建会话</button>" in sidebar


def test_search_also_matches_agent_name() -> None:
    """搜「周报助手」也要能找到会话，而不是只能搜提问正文。"""
    service = _read("app/services/ai/history_query.py")

    assert "def agent_name_matching_clause" in service
    assert "AIAgent.name.like(pattern)" in service
    assert "AIAgent.display_name.like(pattern)" in service
    assert "agent_name_matching_clause(pattern, turn)" in service
    assert "agent_name_matching_clause(pattern)," in service


def test_theme_is_remembered_per_mode() -> None:
    """每个模式记住自己上次的主题，切回来不再被默认值覆盖。"""
    ui_mode = _read("frontend/src/composables/useUiMode.ts")

    assert "const THEME_CACHE_PREFIX" in ui_mode
    assert "export function rememberThemeForMode" in ui_mode
    assert "rememberThemeForMode(previous)" in ui_mode
    assert 'return readModeTheme(mode) ?? (mode === "user" ? "light" : "dark")' in ui_mode
    # 同值切换直接返回：不要因为「再点一次当前模式」把主题重置
    assert "if (previous === mode) return true" in ui_mode


def test_widget_reports_active_conversation_to_host() -> None:
    """挂件 → 宿主：会话建立/切换后把 conversation_id 同步出去。"""
    embed = _read("frontend/src/views/EmbedChat.vue")
    chat = _read("frontend/src/views/Chat.vue")

    assert 'postMessageToHost({ type: "CONVERSATION_ACTIVE", conversation_id: target })' in embed
    assert "CONVERSATION_ACTIVE" in chat
    assert "const syncActiveConversationToUrl = (conversationId: unknown) =>" in chat
    assert "skipNextQueryInit = true" in chat
    assert "delete nextQuery.new" in chat


def test_sidebar_refreshes_when_host_conversation_changes() -> None:
    """宿主 URL 换了会话：新会话要自动进列表，但自己点开的那条不重复拉取。"""
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert 'String(route.query.conversation_id || "")' in sidebar
    assert "selfOpenedConversationId" in sidebar
    assert "void loadTasks()" in sidebar


def test_workbench_icon_is_not_the_more_group_icon(menu: str) -> None:
    """「更多」和「我的工作台」不能共用同一个图标，否则看着像同一个分组。"""
    more_icon = "widget"
    assert f'key: "more",\n    label: "更多",\n    icon: "{more_icon}"' in menu
    assert '{ key: "workbench", label: "我的工作台", icon: "rectangle_stack"' in menu
    assert '"rectangle_stack"' in menu
    # 图标 key 必须在 UserModeIconKey 的白名单里，否则类型/渲染都会回落
    assert '| "rectangle_stack"' in menu


def test_new_conversation_refreshes_task_list_on_stream_end() -> None:
    """新建会话后任务列表要自己出现：挂件流结束重报会话 → 宿主转发 → 侧栏补刷。"""
    chat = _read("frontend/src/views/Chat.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    # 挂件：本轮结束再报一次（URL 不变时也要让宿主知道）
    assert "本轮结束：把当前会话再报一次给宿主" in embed
    assert 'postMessageToHost({ type: "CONVERSATION_ACTIVE", conversation_id: conversationId.value });' in embed

    # 宿主：把会话活跃事件转成窗口事件（同源页面内可订阅）
    assert "nanzi:conversation-active" in chat
    assert "new CustomEvent('nanzi:conversation-active'" in chat

    # 侧栏：监听 + 按 id 补刷，找到就停
    assert 'window.addEventListener("nanzi:conversation-active", onConversationActive)' in sidebar
    assert "refreshTasksForConversation" in sidebar
    assert "attempt >= 3" in sidebar


def test_group_header_does_not_highlight_with_active_child(sidebar: str) -> None:
    """分组只负责展开/收起：子项选中时父项不跟着高亮，否则一屏两个「选中」。"""
    assert "navItemClass(item, false)" in sidebar
    assert "navItemClass(item, isChildActive(item))" not in sidebar
    # 子项选中仍要高亮
    assert "navSubClass(isActive(child.path), child.locked)" in sidebar
