from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_data_portal_home_composable_loads_home_and_scenes_independently():
    source = _read("frontend/src/composables/useDataPortalHome.ts")

    assert '"/api/portal/data-portal/home"' in source
    assert '"/api/v1/chat/dataset-menu"' in source
    assert "Promise.allSettled" in source
    assert "homeError" in source
    assert "sceneError" in source
    assert "homePayload.value =" in source
    assert "scenePayload.value =" in source


def test_data_portal_home_has_real_attention_reports_scenes_and_catalog_sections():
    overview = _read("frontend/src/components/data-portal/DataPortalOverview.vue")
    reports = _read("frontend/src/components/data-portal/DataPortalReportSection.vue")
    scenes = _read("frontend/src/components/data-portal/DataPortalSceneSection.vue")
    catalog = _read("frontend/src/components/data-portal/DataPortalCatalogSection.vue")

    assert "failed_runs_today" in overview
    assert "digests_today" in overview
    assert "active_subscriptions" in overview
    assert "open-activity" in overview
    for value in ("subscribed", "pinned", "favorite", "shared", "recent"):
        assert value in reports
    assert "quick-question" in scenes
    assert "payload.groups" in scenes
    assert "related_data" in catalog
    assert "指标中心" not in catalog


def test_data_portal_full_page_route_and_mobile_navigation_contract():
    router = _read("frontend/src/router/index.ts")
    page = _read("frontend/src/views/DataPortalHome.vue")
    dashboard = _read("frontend/src/views/Dashboard.vue")

    assert "name: 'PersonalCenter'" in router
    assert "path: 'data-portal'" not in router
    for section in ("home", "reports", "scenes", "catalog"):
        assert section in page
    assert "md:hidden" in page
    assert "report_id" in page
    assert "run_id" in page
    assert "conversation_id" in page
    assert "name: '我的数据门户'" not in dashboard


def test_data_portal_workspace_is_flush_with_dashboard_content_area():
    dashboard = _read("frontend/src/views/Dashboard.vue")
    page = _read("frontend/src/views/DataPortalHome.vue")

    assert "['AIChat', 'PersonalCenter'].includes(route.name as string) ? 'p-0'" in dashboard
    assert "props.embedded ? 'bg-white dark:bg-gray-900'" in page
    assert "props.embedded ? 'min-h-[620px]'" in page
    assert "max-w-[1500px]" not in page
    assert "mx-auto" not in page
    assert "rounded-3xl" not in page


def test_portal_drawer_exposes_full_page_entry_to_both_chat_hosts():
    drawer = _read("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert 'event: "open-full-page"' in drawer
    assert "emit('open-full-page')" in drawer
    assert '@open-full-page="openFullDataPortal"' in embed
    assert '@open-full-page="openFullDataPortal"' in debug
    assert 'type: "OPEN_DATA_PORTAL_FULL"' in embed
    assert "window.parent !== window" in embed
    assert 'router.push({ path: "/dashboard/personal", query: { tab: "data" } })' in debug


def test_dashboard_chat_host_handles_full_portal_navigation_from_iframe():
    chat = _read("frontend/src/views/Chat.vue")

    assert "useRouter" in chat
    assert "data.type === 'OPEN_DATA_PORTAL_FULL'" in chat
    assert "router.push({ path: '/dashboard/personal', query: { tab: 'data' } })" in chat


def test_portal_recommended_question_is_forwarded_into_embedded_chat():
    page = _read("frontend/src/views/DataPortalHome.vue")
    chat = _read("frontend/src/views/Chat.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert "portal_question" in page
    assert "portal_question:" in chat
    assert "data.portal_question?.query" in embed
    assert "handlePortalQuickQuestion" in embed


def test_data_portal_catalog_supports_search_structure_and_table_actions():
    types = _read("frontend/src/composables/useDatasetPortal.ts")
    catalog = _read("frontend/src/components/data-portal/DataPortalCatalogSection.vue")
    page = _read("frontend/src/views/DataPortalHome.vue")

    assert "table_columns?: Record<string, DatasetPortalColumn[]>" in types
    assert 'placeholder="搜索数据集、表或字段"' in catalog
    assert "结构说明" in catalog
    assert "查询明细" in catalog
    assert "推荐提问" in catalog
    assert 'emit("quick-question"' in catalog
    assert ':compact="true"' in page
    assert '@quick-question="openQuestion"' in page


def test_data_portal_full_reports_load_all_and_restore_filter_from_query():
    composable = _read("frontend/src/composables/useDataPortalHome.ts")
    reports = _read("frontend/src/components/data-portal/DataPortalReportSection.vue")
    page = _read("frontend/src/views/DataPortalHome.vue")

    assert 'axios.get("/api/portal/saved-reports"' in composable
    assert 'params: { scope: "all" }' in composable
    assert "allReports" in composable
    assert 'value: "all"' in reports
    assert "initialFilter" in reports
    assert 'query: { ...route.query, filter:' in page


def test_data_portal_scenes_support_refresh_send_and_fill_actions():
    scenes = _read("frontend/src/components/data-portal/DataPortalSceneSection.vue")
    page = _read("frontend/src/views/DataPortalHome.vue")

    assert '"/api/v1/chat/dataset-menu/refresh-group-questions"' in scenes
    assert "exclude_questions" in scenes
    assert "换一批" in scenes
    assert "填入" in scenes
    assert "'send'" in scenes
    assert "'fill'" in scenes
    assert '<DataPortalSceneSection v-if="scenePayload" :payload="scenePayload" :compact="true"' in page


def test_data_portal_lives_in_personal_center_data_tab():
    personal = _read("frontend/src/views/PersonalCenter.vue")
    router = _read("frontend/src/router/index.ts")
    dashboard = _read("frontend/src/views/Dashboard.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert "DataPortalHome" in personal
    assert "activeTab === 'data'" in personal
    assert "我的数据门户" in personal
    assert "route.query.tab" in personal
    assert "name: '我的数据门户'" not in dashboard
    assert "redirect: (to" not in router
    assert 'path: "/dashboard/personal", query: { tab: "data" }' in embed
    assert 'path: "/dashboard/personal", query: { tab: "data" }' in debug


def test_personal_center_is_flush_and_tab_query_does_not_remount_page():
    personal = _read("frontend/src/views/PersonalCenter.vue")
    dashboard = _read("frontend/src/views/Dashboard.vue")

    assert 'class="min-h-full bg-white"' in personal
    assert "rounded-lg shadow-sm border border-gray-200" not in personal
    assert "activeTab === 'data' ? '' : 'px-4 pb-4 sm:px-6 sm:pb-6'" in personal
    assert "['AIChat', 'PersonalCenter'].includes(route.name as string) ? 'p-0'" in dashboard
    assert ':key="$route.path"' in dashboard
    assert ':key="$route.fullPath"' not in dashboard
