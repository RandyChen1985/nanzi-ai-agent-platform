import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workbench_composable_uses_one_aggregate_endpoint_and_keeps_stale_data():
    source = _read("frontend/src/composables/useWorkbenchHome.ts")

    assert '"/api/portal/workbench/home"' in source
    assert "payload.value = next" in source
    assert "payload.value = null" not in source
    assert "工作台暂时无法更新，已保留最近一次成功内容。" in source
    assert "refreshing" in source
    assert "silent" in source
    assert "stableSnapshot" in source
    assert "generated_at" in source
    for fragment in ("loading", "error", "refresh"):
        assert fragment in source


def test_workbench_page_lays_out_main_column_and_sidebar_without_zero_cards():
    page = _read("frontend/src/views/PersonalWorkbench.vue")

    # 三种 mode 都要有对应呈现：待处理 / 安静态一行提示 / 新用户引导
    assert 'payload.value?.mode === "quiet"' in page
    assert 'payload.value?.mode === "new_user"' in page
    assert "今天没有待处理事项" in page
    assert "欢迎使用 ${branding.product_name" in page
    assert "浏览场景包" in page

    # 双栏：主列放「要处理的事」，侧栏放「我有的东西」
    assert "xl:grid-cols-[minmax(0,1fr)_336px]" in page
    assert "xl:sticky" in page
    assert "WorkbenchResourceGrid" in page
    assert "WorkbenchAgents" in page

    # 三类活动合并成一条时间线，不再各占一张卡片
    assert "WorkbenchFeed" in page
    assert "feedItems" in page
    for key in ("resume_items", "latest_results", "recent_tasks"):
        assert key in page

    # 空态由「这一块有没有数据」决定：待处理为 0 时不留一块写着「暂无」的空卡片
    assert 'v-if="payload.attention.length"' in page
    assert 'v-if="payload.running_items.length"' in page

    # 已下线的旧卡片不得回来
    for retired in ("WorkbenchResults", "WorkbenchResume", "WorkbenchTasks", "WorkbenchMobileViewAll"):
        assert retired not in page
    assert "今日运行正常" not in page
    assert "待处理 0" not in page

    # 顶栏刷新按钮的口径保持不变
    assert "workbench-refresh-btn" in page
    assert "hover:bg-blue-50 hover:text-blue-600" in page
    assert ">刷新<" in page or "'刷新'" in page

    # 数据源异常要能被看见
    assert "failedSources" in page
    assert "部分数据暂时无法获取" in page

    # 路由动作仍然闭合
    assert "openMyTasks" in page
    assert 'tab: "tasks"' in page
    assert 'view: "history"' in page
    assert "open_task_run" in page
    assert "open_task" in page
    assert "next_scheduled_item" in page
    assert "personal_resources" in page
    assert "useBranding" in page


def test_every_backend_resource_key_has_an_icon():
    """后端 PERSONAL_RESOURCE_DEFS 新增一项时，前端图标表必须同步。

    漏配不会报错，只会落到 fallback 的「灰色感叹号」——看图的人会以为这一项出错了，
    比没有图标更糟（`WorkbenchPersonalResources.vue` 的注释也提过同一个坑，但没有测试守住）。
    """
    from app.services.workbench_home_service import PERSONAL_RESOURCE_DEFS

    grid = _read("frontend/src/components/workbench/WorkbenchResourceGrid.vue")
    block = grid[grid.index("const resourcePaths"):grid.index("const fallbackPaths")]
    configured = set(re.findall(r"^  (\w+): \[", block, flags=re.MULTILINE))
    expected = {spec["key"] for spec in PERSONAL_RESOURCE_DEFS}

    assert expected <= configured, f"以下资源 key 还没配图标: {sorted(expected - configured)}"


def test_workbench_route_navigation_and_actions_are_closed():
    router = _read("frontend/src/router/index.ts")
    dashboard = _read("frontend/src/views/Dashboard.vue")
    page = _read("frontend/src/views/PersonalWorkbench.vue")
    login = _read("frontend/src/views/Login.vue")

    from app.services.menu_seed import BUILTIN_BY_CODE

    assert "path: 'workbench'" in router
    assert "name: 'PersonalWorkbench'" in router
    # 侧栏入口已入库：文案与「无权限码、人人可见」都由菜单数据决定，不再写死在 Dashboard
    workbench = BUILTIN_BY_CODE["menu:workbench"]
    assert workbench.label == "我的工作台"
    assert workbench.permission_code is None
    assert "SidebarMenuLink" in dashboard
    assert 'if (route.name === "PersonalWorkbench") return "p-0 sm:px-4 sm:pt-2.5 sm:pb-4 md:px-6 md:pt-3 md:pb-6 lg:px-8 lg:pt-3.5 lg:pb-6"' in dashboard
    # 落地页可在「菜单管理」里配置，因此 homeRoute 先取 landingPath，未配置时才退回角色口径
    assert (
        "landingPath.value || (userInfo.value.role === 'admin' ? '/dashboard' : '/dashboard/workbench')"
        in dashboard
    )
    assert ':to="homeRoute"' in dashboard
    assert "router.push('/dashboard/workbench')" in login
    for action in (
        "open_task_log",
        "open_task",
        "open_digest",
        "open_report",
        "open_conversation",
        "open_agent",
        "open_scenario",
    ):
        assert action in page
    assert 'dataset_portal: "1"' in page


def test_workbench_components_emit_actions_and_drop_decorative_noise():
    attention = _read("frontend/src/components/workbench/WorkbenchAttention.vue")
    running = _read("frontend/src/components/workbench/WorkbenchRunning.vue")
    feed = _read("frontend/src/components/workbench/WorkbenchFeed.vue")
    agents = _read("frontend/src/components/workbench/WorkbenchAgents.vue")
    scenarios = _read("frontend/src/components/workbench/WorkbenchScenarios.vue")
    resources = _read("frontend/src/components/workbench/WorkbenchResourceGrid.vue")
    display = _read("frontend/src/utils/workbenchDisplay.ts")

    for component, event in (
        (attention, "open-item"),
        (attention, "view-all"),
        (running, "open-item"),
        (feed, "open-item"),
        (feed, "view-all"),
        (agents, "open-agent"),
        (scenarios, "open-scenario"),
    ):
        assert event in component
    assert "emit('select'" in resources or 'emit("select"' in resources

    # 左侧竖条是「需要你处理」唯一的常驻色彩
    assert "bg-red-500" in attention

    # 活动流按类型筛选：三类活动混排在一条时间线上，靠 tab 回到单一类型
    for value in ('value: "conversation"', 'value: "report"', 'value: "task"'):
        assert value in feed
    assert "resolveWorkbenchKind" in feed

    # 列表没有数据时给下一步，而不是留一块虚线占位
    assert "还没有生成过产出" in feed
    assert "还没有会话" in feed
    assert "当前还没有可用的业务场景" in scenarios

    # 以下都是旧实现里被点出来的具体问题，不允许回退
    assert "animate-pulse" not in running  # 固定 33% 的假进度条
    assert "w-1/3" not in running
    for placeholder in ("更多会话会出现在这里", "更多产出会出现在这里", "更多执行记录会出现在这里"):
        assert placeholder not in feed
    assert "来源：" not in attention
    assert "来源：" not in running
    for emoji in ("⏱", "📰", "📊", "💬", "🔔", "🤖"):
        assert emoji not in display

    assert "formatWorkbenchRelativeTime" in display
    assert "formatWorkbenchDurationMs" in display
    assert "workbenchActionLabel" in display
    assert 'open_task_run' in display
    assert 'scheduled: "活跃"' in display
    assert 'stopped: "已暂停"' in display


def test_workbench_personal_resource_cards_link_to_personal_tabs():
    page = _read("frontend/src/views/PersonalWorkbench.vue")
    cards = _read("frontend/src/components/workbench/WorkbenchPersonalResources.vue")
    types = _read("frontend/src/types/workbench.ts")

    assert "WorkbenchPersonalResources" in page
    assert "personal_resources" in page
    assert "personal_resources" in types
    assert 'path: "/dashboard/personal"' in page
    assert "query: { tab: item.tab }" in page
    assert "formatTokenCompact" in cards
    for key in ("memory", "tokens", "data", "skills", "mcp", "tasks", "inbox"):
        assert key in _read("app/services/workbench_home_service.py")
    assert "filterWorkbenchPersonalResources" in page
    assert "WORKBENCH_HIDDEN_RESOURCE_KEYS" in _read("frontend/src/constants/personalResources.ts")
    assert "openPortalInboxPanel" in page
    assert "isInboxPersonalResource" in page
    assert "sm:grid-cols-5" in cards or "xl:grid-cols-6" in cards


def test_workbench_personal_resources_emits_select_instead_of_router():
    source = _read("frontend/src/components/workbench/WorkbenchPersonalResources.vue")
    assert 'emit("select"' in source or "emit('select'" in source
    assert 'path: "/dashboard/personal"' not in source
    assert "useRouter" not in source


def test_personal_workbench_wires_resource_select_to_personal_center():
    source = _read("frontend/src/views/PersonalWorkbench.vue")
    assert "WorkbenchPersonalResources" in source
    assert "@select=" in source
    assert 'path: "/dashboard/personal"' in source


def test_task_center_accepts_workbench_task_target():
    source = _read("frontend/src/views/TaskCenter.vue")

    assert "useRoute" in source
    assert "route.query.task_id" in source
    assert "openLogs(target)" in source


def test_chat_host_forwards_workbench_conversation_and_agent_targets():
    chat = _read("frontend/src/views/Chat.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert "conversation_id: route.query.conversation_id" in chat
    assert "agent_id: route.query.agent_id" in chat
    assert "watch(" in chat
    assert "sendInitConfig()" in chat
    assert "requestedConversationId" in embed
    assert "data.conversation_id" in embed
    assert "switchToExpert(agentId)" in embed
    assert "if (data.agent_id)" in embed


def test_new_session_clears_workbench_conversation_pin():
    """从工作台带 conversation_id 进入后，新会话不得再被 resume id / URL 钉回旧会话。"""
    chat = _read("frontend/src/views/Chat.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert "requestedConversationId = \"\"" in embed
    assert "clear_host_conversation_pin" in embed
    assert 'type: "CONVERSATION_CHANGED"' in embed
    assert "clearHostConversationPin" in chat
    assert "clear_host_conversation_pin" in chat
    assert "skipNextQueryInit" in chat
    assert "delete nextQuery.conversation_id" in chat


def test_scenario_browse_routes_allow_chat_users():
    router = _read("frontend/src/router/index.ts")
    assert "path: 'scenario-templates'" in router
    assert "meta: { perm: 'menu:ai_chat', title: '场景模板' }" in router
    assert "meta: { perm: 'menu:ai_chat', title: '模板详情' }" in router
    assert "meta: { perm: 'menu:agent_management', title: '交付向导' }" in router


def test_workbench_agent_cards_prefer_agent_avatar():
    """助手卡片必须优先展示智能体自己的头像，没配置时才回退名称首字。"""
    agents = _read("frontend/src/components/workbench/WorkbenchAgents.vue")
    workbench_type = _read("frontend/src/types/workbench.ts")

    assert "agentOwnAvatarUrl" in agents
    assert 'class="h-full w-full object-cover"' in agents
    assert "{{ initial(agent.name) }}" in agents
    assert "avatar_url?: string" in workbench_type
