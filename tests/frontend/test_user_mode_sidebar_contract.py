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
    """一级入口固定为：新建任务 / 专家技能连接器 / 定时任务 / 知识库 / 工作空间 / 更多。"""
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
    assert "查看全部会话" in sidebar


def test_dashboard_switches_sidebar_by_mode(dashboard: str) -> None:
    assert "UserModeSidebar" in dashboard
    assert "isUserMode" in dashboard
    # 用户模式固定 252，菜单文案更长；专业模式维持原来的折叠宽度
    assert 'if (isUserMode.value) return "w-[252px]"' in dashboard
    # 专业模式仍然渲染动态菜单
    assert "visibleNavNodes" in dashboard


def test_new_chat_remounts_chat_page_when_already_there(dashboard: str) -> None:
    """已经在聊天页时再点新建任务，必须换 key 重挂载，否则看起来「点了没反应」。"""
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
    # 专家组子项全无权限 → 整组丢弃；部分有权限 → 只留有的那个
    assert payload["hasExpertsGroup"] is False
    assert payload["partialExperts"] == ["skill"]


def test_new_task_opens_a_blank_conversation_not_the_last_one() -> None:
    """「新建任务」必须跳过会话续接，开一条空白会话。

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
    """用户模式登录直接落在「新建任务」，不绕概览/工作台。"""
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
    assert 'if (path === "/dashboard" || path === "/dashboard/") return route.path === "/dashboard"' in sidebar


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


def test_open_history_actually_opens_the_sidebar() -> None:
    """「查看全部会话」要真的打开历史侧栏。

    原来只是 push('/dashboard/chat')：已经在聊天页时什么都不发生，
    从别处跳过来侧栏也是关的。
    """
    dashboard = _read("frontend/src/views/Dashboard.vue")
    chat = _read("frontend/src/views/Chat.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")

    # 宿主：冷启动走 URL，已在聊天页走事件
    assert 'query: { history: "1" }' in dashboard
    assert "portal:open-history" in dashboard
    assert "window.dispatchEvent(new CustomEvent(OPEN_HISTORY_EVENT))" in dashboard

    # iframe 宿主：透传 + 事件转发
    assert "embedQuery.set('history', '1')" in chat
    assert "postMessage({ type: 'OPEN_HISTORY' }" in chat

    # 挂件：两个入口都落到同一个状态
    assert 'query.get("history") === "1"' in embed
    assert 'case "OPEN_HISTORY":' in embed
    assert "showHistorySidebar.value = true;" in embed


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


def test_only_the_active_group_stays_expanded() -> None:
    """路由变化时，其他分组要收起。

    之前只写 `expanded[key] = true`、从不置回 false：点开过「更多」之后再点定时任务，
    那一组会一直挂在下面，看起来像「点别的菜单把更多也展开了」。
    """
    sidebar = _read("frontend/src/components/layout/UserModeSidebar.vue")

    assert "const containsCurrent = item.children.some(" in sidebar
    assert "expanded[item.key] = containsCurrent" in sidebar


def test_professional_mode_defaults_to_dark_theme() -> None:
    """切到专业模式要回到深色。

    这是平台原本的视觉；用户模式的亮色是简化布局的配套。
    两条路（切换 + 登录落地）都要覆盖，否则从用户模式切过去会停在亮色。
    """
    ui_mode = _read("frontend/src/composables/useUiMode.ts")
    login = _read("frontend/src/views/Login.vue")

    assert "export function applyProModeDefaultTheme" in ui_mode
    assert 'setTheme("dark")' in ui_mode
    assert "else applyProModeDefaultTheme()" in ui_mode
    assert "applyProModeDefaultTheme()" in login
