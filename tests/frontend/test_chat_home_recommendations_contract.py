"""方案 A 欢迎页：行动卡、近 3 天推荐与资源状态条契约。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_welcome_dashboard_exposes_recommendation_affordance_without_border():
    dashboard = _source("frontend/src/components/embed/WelcomeDashboard.vue")

    assert "开始一件事" in dashboard
    assert "基于近 3 天会话" in dashboard
    assert "生成推荐" in dashboard
    assert "换一批" in dashboard
    assert "recommendationsLoading" in dashboard
    assert "recommendationSource" in dashboard
    assert "recommendationsError" in dashboard
    assert "emit('generate-recommendations')" in dashboard
    assert "emit('fill-question', card.prompt)" in dashboard
    assert "emit('quick-question', card.prompt)" in dashboard

    # 按钮和资源刷新一样走幽灵态，不再带描边/主色背景。
    button_click = dashboard.index('@click="emit(\'generate-recommendations\')"')
    button_block = dashboard[button_click - 700 : button_click + 80]
    assert "border" not in button_block
    assert "text-gray-300" in button_block


def test_welcome_dashboard_renders_recommendation_source_and_skeleton():
    dashboard = _source("frontend/src/components/embed/WelcomeDashboard.vue")

    assert "recommendation-skeleton" in dashboard
    assert "actionCardFooter" in dashboard
    assert "actionCardKicker" in dashboard
    assert "为你推荐" in dashboard
    assert "welcome-card-item" in dashboard
    assert 'data-tour="welcome-cards"' in dashboard


def test_embed_chat_wires_recent_context_and_recommendation_endpoints():
    embed = _source("frontend/src/views/EmbedChat.vue")

    assert "/api/v1/chat/home-context" in embed
    assert "/api/v1/chat/home-recommendations" in embed
    assert "loadWelcomeHomeContext" in embed
    assert "generateWelcomeRecommendations" in embed
    # Cookie 登录时 config.token 可能为空，effective agent watch 不能只等 token 分支。
    assert "void loadWelcomeCards(agentId);" in embed
    assert "showToast(\"已根据最近" not in embed
    assert "showToast(message, \"error\")" not in embed
    assert "openWelcomeRecentConversation" in embed
    assert '@generate-recommendations="generateWelcomeRecommendations"' in embed
    assert '@fill-question="handleQuickQuestion($event, \'fill\')"' in embed
    assert ':recent-conversations="welcomeRecentConversations"' in embed


def test_compact_resource_grid_renders_as_segmented_strip():
    resources = _source("frontend/src/components/workbench/WorkbenchPersonalResources.vue")

    assert "sm:grid-cols-5" in resources
    assert "bg-gray-50/60" in resources
    assert "last:border-r-0" in resources
