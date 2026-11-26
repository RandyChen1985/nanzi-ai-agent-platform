import re
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "frontend/src/components/chatbi/SavedReportItemCard.vue"
QUICK_VIEWS = ROOT / "frontend/src/components/chatbi/SavedReportQuickViews.vue"
SECTION = ROOT / "frontend/src/components/data-portal/DataPortalReportSection.vue"
HOME = ROOT / "frontend/src/views/DataPortalHome.vue"
DETAIL = ROOT / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue"
RUN_MODAL = ROOT / "frontend/src/components/chat/SavedReportRunModal.vue"
WORKFLOW = ROOT / "frontend/src/composables/chat/useSavedReportWorkflow.ts"


def test_saved_report_card_has_text_primary_actions_and_more_menu():
    source = CARD.read_text(encoding="utf-8")

    assert "运行" in source
    assert "详情" in source
    assert "更多操作" in source
    assert "点击打开详情" not in source


def test_quick_views_keep_title_area_readable_in_narrow_portal_panel():
    source = QUICK_VIEWS.read_text(encoding="utf-8")

    assert 'class="min-w-0 flex-1 truncate' in source
    assert 'class="mt-2 flex items-center justify-between' in source
    assert 'class="min-h-9 shrink-0 rounded-lg border border-primary/20 bg-primary/5' in source
    assert 'text-primary-dark' in source
    assert 'class="min-h-9 shrink-0 rounded-lg bg-primary' not in source
    assert 'class="mt-3 w-full' not in source


def test_saved_report_create_actions_use_svg_plus_and_keep_primary_blue_only():
    for path in (SECTION, DETAIL):
        source = path.read_text(encoding="utf-8")

        assert "➕" not in source
        assert 'stroke="currentColor"' in source
        assert 'd="M12 5v14M5 12h14"' in source

    assert "➕" not in HOME.read_text(encoding="utf-8")
    quick_views = QUICK_VIEWS.read_text(encoding="utf-8")
    assert not re.search(r"(?<![/\w-])bg-primary(?![/\w-])", quick_views), "快捷视图只用软主色（bg-primary/5），不能出现实心主色底"


def test_data_portal_reports_tab_has_one_create_report_entrypoint():
    source = HOME.read_text(encoding="utf-8")

    assert '@click="openCreateReport"' not in source
    assert '@create-report="openCreateReport"' in source


def test_saved_report_more_menu_expands_card_without_covering_content():
    source = CARD.read_text(encoding="utf-8")

    assert "<teleport to=\"body\">" in source
    assert 'class="fixed inset-0 z-[320]' in source
    assert 'position: "fixed"' in source
    assert "nextTick" in source
    assert "menuRef" in source
    assert "actualMenuHeight" in source
    assert "rect.top - actualMenuHeight - gap" in source


def test_report_section_exposes_quick_views_and_shared_filter_semantics():
    source = SECTION.read_text(encoding="utf-8")

    for label in ("最近运行", "常用报表", "订阅中"):
        assert label in source
    assert "共享给我" in source
    assert "border-primary/20 bg-primary/5 text-primary-dark" in source
    assert "activeFilter === option.value ?" in source


def test_saved_report_detail_uses_management_tabs_and_primary_run_action():
    source = DETAIL.read_text(encoding="utf-8")

    for label in ("报表概览", "运行历史", "订阅与共享", "运行报表", "更多操作"):
        assert label in source


def test_run_modal_exposes_actual_scope_and_permission_state():
    source = RUN_MODAL.read_text(encoding="utf-8")

    assert "本次查询范围" in source
    assert "实际执行 SQL" in source
    assert "权限预检" in source
    assert "报表描述与业务口径说明" in source
    assert "pendingReport?.description" in source


def test_saved_report_run_preserves_description_from_portal_card():
    source = DETAIL.read_text(encoding="utf-8")

    assert "description: report.description" in source


def test_saved_report_views_support_wide_and_compact_switchers():
    section = SECTION.read_text(encoding="utf-8")
    panel = DETAIL.read_text(encoding="utf-8")
    card = CARD.read_text(encoding="utf-8")

    assert "reportViewMode" in section
    assert "nanzi_saved_report_portal_view" in section
    assert 'return isReportViewMode(stored) ? stored : "list"' in section
    assert 'return "list"' in section
    assert "切换到卡片视图" in section
    assert "切换到列表视图" in section
    assert ':variant="reportViewMode"' in section

    assert "savedReportViewMode" in panel
    assert "nanzi_saved_report_panel_view" in panel
    assert 'return isSavedReportViewMode(stored) ? stored : "list"' in panel
    assert 'return "list"' in panel
    assert "切换报表视图" in panel
    assert ':variant="savedReportViewMode"' in panel

    assert 'variant?: "card" | "list"' in card
    assert "variant === 'list'" in card or 'variant === "list"' in card


def test_saved_report_result_separates_query_and_analysis_states():
    source = WORKFLOW.read_text(encoding="utf-8")

    for label in ("查询成功", "业务解读", "重试解读"):
        assert label in source
    assert "业务解读暂不可用。" not in source
