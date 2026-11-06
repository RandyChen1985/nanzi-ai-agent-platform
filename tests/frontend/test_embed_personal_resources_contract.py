"""Contract: Embed「我的资源」弹层壳与懒加载 Tab。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_personal_resources_modal_shell_and_tabs():
    modal = _source("frontend/src/components/embed/PersonalResourcesModal.vue")
    assert "我的资源" in modal
    assert "defineAsyncComponent" in modal
    assert "PersonalMemoryPanel" in modal
    assert "PersonalTokenUsage" in modal
    assert "DataPortalHome" in modal
    assert "SkillsManagement" in modal
    assert "McpManagement" in modal
    assert "TaskCenter" in modal
    assert "update:visible" in modal
    assert "activeTab" in modal
    # Embed 弹层须传 delegate-navigation；PersonalCenter 仅传 embedded
    assert "delegate-navigation" in modal or "delegateNavigation" in modal
    # 弹层 Tab 不含站内消息（点击资源卡改为打开铃铛）
    assert "PERSONAL_RESOURCE_MODAL_TABS" in modal
    assert "我的站内消息" not in modal


def test_welcome_dashboard_renders_action_cards_before_personal_resources():
    dashboard = _source("frontend/src/components/embed/WelcomeDashboard.vue")
    assert "WorkbenchPersonalResources" in dashboard
    assert "open-personal-resources" in dashboard
    assert "refresh-personal-resources" in dashboard
    assert "刷新我的资源" in dashboard
    assert "personalResourcesRefreshing" in dashboard
    assert "开始一件事" in dashboard
    # 方案 A：先给“下一步能做什么”，再把资源状态作为第二层信息。
    actions_pos = dashboard.find("开始一件事")
    resources_pos = dashboard.find("刷新我的资源")
    grid_pos = dashboard.find("grid-cols-1 sm:grid-cols-3")
    assert actions_pos != -1 and resources_pos != -1 and grid_pos != -1
    assert actions_pos < grid_pos < resources_pos


def test_embed_chat_wires_personal_resources_refresh():
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "refreshWorkbenchHome" in embed
    assert "workbenchHomeRefreshing" in embed
    assert 'refresh-personal-resources="refreshWelcomePersonalResources"' in embed or "@refresh-personal-resources=\"refreshWelcomePersonalResources\"" in embed
    assert ":personal-resources-refreshing=" in embed


def test_embed_chat_wires_workbench_home_and_personal_resources_modal():
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "PersonalResourcesModal" in embed
    assert "useWorkbenchHome" in embed or "/api/portal/workbench/home" in embed
    assert "personalResourceFallbackItems" in embed
    assert "open-personal-resources" in embed or "openPersonalResources" in embed
    assert "filterEmbedWelcomePersonalResources" in embed
    constants = _source("frontend/src/constants/personalResources.ts")
    assert '"memory"' in constants and '"data"' in constants
    assert '"inbox"' in constants
    assert "OPEN_PORTAL_INBOX_EVENT" in constants
    assert "EMBED_WELCOME_HIDDEN_RESOURCE_KEYS" in constants
    assert "PERSONAL_RESOURCE_MODAL_TABS" in constants
    assert "PortalNotificationBell" in embed
    assert 'variant="modal"' in embed
    assert "portalInboxRef" in embed
    assert "handleInboxOpenSavedReport" in embed
    bell = _source("frontend/src/components/PortalNotificationBell.vue")
    assert "OPEN_PORTAL_INBOX_EVENT" in bell
    assert "openFromExternal" in bell
    assert "isModalVariant" in bell
    assert 'variant?: "bell" | "modal"' in bell
    cards = _source("frontend/src/components/workbench/WorkbenchPersonalResources.vue")
    assert "sm:grid-cols-5" in cards
    assert "isInboxPersonalResource" in embed
