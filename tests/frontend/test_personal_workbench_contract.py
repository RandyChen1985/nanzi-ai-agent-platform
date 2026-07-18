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
    assert "xl:grid-cols-2" in page
    assert "WorkbenchAttention" in page
    assert "WorkbenchResults" in page
    assert "WorkbenchResume" in page
    assert "WorkbenchAgents" in page
    assert "WorkbenchScenarios" in page
    assert "WorkbenchNextScheduled" in page
    assert "next_scheduled_item" in page
    assert "待处理 0" not in page
    assert "最新结果 0" not in page
    assert "failedSources" in page
    assert "部分数据暂时无法获取" in page
    assert "open_task" in page
    assert "欢迎使用 NanZi" in page
    assert "浏览场景包" in page


def test_workbench_route_navigation_and_actions_are_closed():
    router = _read("frontend/src/router/index.ts")
    dashboard = _read("frontend/src/views/Dashboard.vue")
    page = _read("frontend/src/views/PersonalWorkbench.vue")
    login = _read("frontend/src/views/Login.vue")

    assert "path: 'workbench'" in router
    assert "name: 'PersonalWorkbench'" in router
    assert "我的工作台" in dashboard
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
    agents = _read("frontend/src/components/workbench/WorkbenchAgents.vue")
    scenarios = _read("frontend/src/components/workbench/WorkbenchScenarios.vue")
    display = _read("frontend/src/utils/workbenchDisplay.ts")

    assert "open-item" in attention
    assert "view-all" in attention
    assert "border-l-red-500" in attention
    assert "WorkbenchItemMeta" in results
    assert "去数据门户看看" in results
    assert "去找个助手聊聊" in resume
    assert "最近使用的助手" in agents
    assert "开始对话" in agents
    assert "open-agent" in agents
    assert "当前还没有可用的业务场景" in scenarios
    assert "open-scenario" in scenarios
    assert "agentsAvailable" in scenarios
    assert "formatWorkbenchRelativeTime" in display
    assert "workbenchActionLabel" in display
    assert "workbenchKindLabel" in display
    assert "WorkbenchMobileViewAll" in results


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


def test_scenario_browse_routes_allow_chat_users():
    router = _read("frontend/src/router/index.ts")
    assert "path: 'scenario-templates'" in router
    assert "meta: { perm: 'menu:ai_chat', title: '场景模板' }" in router
    assert "meta: { perm: 'menu:ai_chat', title: '模板详情' }" in router
    assert "meta: { perm: 'menu:agent_management', title: '交付向导' }" in router
