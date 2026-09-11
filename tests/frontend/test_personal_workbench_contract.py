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


def test_workbench_page_has_three_dynamic_states_without_zero_dashboard():
    page = _read("frontend/src/views/PersonalWorkbench.vue")

    assert 'payload.value?.mode === "active"' in page
    assert 'payload.value?.mode === "quiet"' in page
    assert 'payload.value?.mode === "new_user"' in page
    assert "今日运行正常" in page
    assert "summaryPrimary" in page
    assert "bannerMessage" in page
    assert "lg:grid-cols-2" in page
    assert "xl:grid-cols-3" in page
    assert "items-stretch" in page
    assert "workbench-refresh-btn" in page
    assert "hover:bg-blue-50 hover:text-blue-600" in page
    assert "h-9 w-9" not in page.split("workbench-refresh-btn", 1)[1].split("</button>", 1)[0]
    assert ">刷新<" in page or "'刷新'" in page
    assert "有 ${payload.value.resume_items.length} 项工作可以继续" in page
    assert "最近没有新的产出" in page
    assert "WorkbenchAttention" in page
    assert "WorkbenchResults" in page
    assert "WorkbenchResume" in page
    assert "WorkbenchTasks" in page
    assert "recent_tasks" in page
    assert "openMyTasks" in page
    assert 'tab: "tasks"' in page
    assert 'view: "history"' in page
    assert "open_task_run" in page
    assert "WorkbenchAgents" in page
    assert "WorkbenchScenarios" in page
    assert "WorkbenchNextScheduled" in page
    assert "WorkbenchRunning" in page
    assert "payload.running_items" in page
    assert "running_items" in page
    assert "WorkbenchPersonalResources" in page
    assert "personal_resources" in page
    assert "next_scheduled_item" in page
    assert "待处理 0" not in page
    assert "最新结果 0" not in page
    assert "failedSources" in page
    assert "部分数据暂时无法获取" in page
    assert "open_task" in page
    assert "欢迎使用 ${branding.product_name" in page
    assert "useBranding" in page
    assert "浏览场景包" in page


def test_workbench_route_navigation_and_actions_are_closed():
    router = _read("frontend/src/router/index.ts")
    dashboard = _read("frontend/src/views/Dashboard.vue")
    page = _read("frontend/src/views/PersonalWorkbench.vue")
    login = _read("frontend/src/views/Login.vue")

    assert "path: 'workbench'" in router
    assert "name: 'PersonalWorkbench'" in router
    assert "我的工作台" in dashboard
    assert 'if (route.name === "PersonalWorkbench") return "p-0 sm:px-4 sm:pt-2.5 sm:pb-4 md:px-6 md:pt-3 md:pb-6 lg:px-8 lg:pt-3.5 lg:pb-6"' in dashboard
    assert "const homeRoute = computed(() => userInfo.value.role === 'admin' ? '/dashboard' : '/dashboard/workbench')" in dashboard
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


def test_workbench_components_emit_actions_and_show_empty_guidance():
    attention = _read("frontend/src/components/workbench/WorkbenchAttention.vue")
    results = _read("frontend/src/components/workbench/WorkbenchResults.vue")
    resume = _read("frontend/src/components/workbench/WorkbenchResume.vue")
    tasks = _read("frontend/src/components/workbench/WorkbenchTasks.vue")
    agents = _read("frontend/src/components/workbench/WorkbenchAgents.vue")
    scenarios = _read("frontend/src/components/workbench/WorkbenchScenarios.vue")
    running = _read("frontend/src/components/workbench/WorkbenchRunning.vue")
    display = _read("frontend/src/utils/workbenchDisplay.ts")

    assert "open-item" in attention
    assert "view-all" in attention
    assert "来源：未读站内通知" in attention
    assert "border-l-red-500" in attention
    assert "WorkbenchItemMeta" in results
    assert "还没有生成过分析结果" in results
    assert "创建第一份报表" in results
    assert "更多产出会出现在这里" in results
    assert "flex h-full flex-col" in results
    assert "slotCount = 4" in results
    assert "去找个助手聊聊" in resume
    assert "更多会话会出现在这里" in resume
    assert "flex h-full flex-col" in resume
    assert "slotCount = 4" in resume
    assert "最近任务" in tasks
    assert "执行记录" in tasks
    assert "更多执行记录会出现在这里" in tasks
    assert "暂无任务执行记录" in tasks
    assert "formatWorkbenchDurationMs" in tasks
    assert "耗时" in tasks
    assert "WorkbenchItemMeta" not in tasks
    assert "不展示截断摘要" in tasks
    assert "去创建定时任务" in tasks
    assert "tone=\"amber\"" in tasks
    assert "slotCount = 4" in tasks
    assert "最近使用的助手" in agents
    assert "开始对话" in agents
    assert "open-agent" in agents
    assert "当前还没有可用的业务场景" in scenarios
    assert "open-scenario" in scenarios
    assert "agentsAvailable" in scenarios
    assert "formatWorkbenchRelativeTime" in display
    assert "formatWorkbenchDurationMs" in display
    assert "workbenchActionLabel" in display
    assert "workbenchKindLabel" in display
    assert 'open_task_run' in display
    assert 'scheduled: "活跃"' in display
    assert 'stopped: "已暂停"' in display
    assert "WorkbenchMobileViewAll" in results
    assert "进行中" in running
    assert "来源：正在生成的报表" in running
    assert "animate-pulse" in running
    assert "source" in running
    assert "agentscope_pending" not in running
    assert "open-item" in running

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
