"""Tests for ChatBI insight frontend contracts."""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shared_chatbi_insight_contract_is_wired_to_both_chat_surfaces():
    types = _source("frontend/src/types/chatbiInsight.ts")
    reducer = _source("frontend/src/utils/chatbiInsight.ts")
    embed = _source("frontend/src/views/EmbedChat.vue")
    debug = _source("frontend/src/views/AgentDebug.vue")
    panel = _source("frontend/src/components/chatbi/ChatBIInsightPanel.vue")
    result_table = _source("frontend/src/components/chatbi/ChatBIResultTable.vue")

    assert "export interface ChatBIInsightMeta" in types
    assert "export interface ChatBIResultTable" in types
    assert "export interface ChatBIAnalysisScope" in types
    assert "total_row_count: number | null" in types
    assert "returned_row_count?: number" in types
    assert "truncated?: boolean" in types
    assert "table?: ChatBIResultTable | null" in types
    assert "analysis_scope?: ChatBIAnalysisScope | null" in types
    assert "actions: ChatBIInsightAction[]" in types
    assert "applyChatBIInsightEvent" in reducer
    assert "chatbi_insight_meta" in reducer
    assert 'label: "明细"' in panel
    assert 'label: "依据"' in panel
    assert "引用来源" in panel
    assert "匹配总数" in panel
    assert "总数未统计" in panel
    assert "总数未统计" in result_table
    assert "totalCountLabel" in result_table
    # 面板本体是极简页签式（根容器仅保留顶部分隔线），不应被包成圆角边框卡片；
    # 明细页的导出格式下拉浮层是局部弹层，不改变面板本体的无边框设计。
    panel_root = panel.split("<template>", 1)[1].split(">", 1)[0]
    assert "border-t border-gray-100" in panel_root
    assert "rounded-xl border border-gray-200" not in panel_root
    for source in (embed, debug):
        assert "ChatBIInsightPanel" in source
        assert "ChatBIContinueAnalysis" in source
        assert "chatbiInsight?: ChatBIInsightMeta" in source
        assert "applyChatBIInsightEvent" in source
        assert ':citations="msg.citations"' in source
        assert "@open-citation=" in source
        assert 'uppercase tracking-wider flex-1">引用来源' not in source


def test_continue_analysis_uses_one_trigger_and_responsive_chooser():
    source = _source("frontend/src/components/chatbi/ChatBIContinueAnalysis.vue")

    assert source.count('@click="open = true"') == 1
    assert "isMobile" in source
    assert "fixed inset-0" in source
    assert "absolute bottom-full" in source
    assert "emit('select', action.query)" in source
    assert "props.actions.slice(0, 6)" in source
    assert 'aria-label="关闭继续分析"' in source
    assert "handleDocumentPointerDown" in source
    assert "@mouseleave=\"scheduleClose\"" in source
    assert "@mouseenter=\"cancelScheduledClose\"" in source
    assert "@focusout=\"handleFocusOut\"" in source
    assert "@keydown.esc=\"closeChooser\"" in source


def test_data_evidence_hides_sql_until_expanded():
    source = _source("frontend/src/components/chatbi/ChatBIDataEvidence.vue")

    assert "查看数据依据" in source
    assert "showDetails" in source
    assert "showSql" in source
    assert "meta.final_sql" in source
    assert "已按你的数据权限自动过滤结果" in source


def test_data_evidence_panel_shows_source_and_freshness_metadata():
    types = _source("frontend/src/types/chatbiInsight.ts")
    source = _source("frontend/src/components/chatbi/ChatBIDataEvidence.vue")

    assert "export interface ChatBIEvidenceMeta" in types
    assert "evidence?: ChatBIEvidenceMeta" in types
    for label in ("证据状态", "来源标识", "观测时间", "数据截至", "数据时效"):
        assert label in source
    for field in ("result_status", "source_ref", "observed_at", "source_as_of", "freshness"):
        assert f"meta.evidence?.{field}" in source


def test_insight_panel_is_minimal_tabbed_and_borderless():
    panel = _source("frontend/src/components/chatbi/ChatBIInsightPanel.vue")

    assert "明细" in panel
    assert "依据" in panel
    assert "引用来源" in panel
    assert 'id: "citations"' in panel
    assert "toggleTab" in panel
    assert "activeTab" in panel
    assert "AI 样例" in panel
    assert "border-t border-gray-100" in panel
    assert "tracking-wider" in panel
    assert "citation-chip" in panel
    assert "mb-3" in panel
    # 面板根容器只保留顶部分隔线，而非整体圆角边框卡片（导出下拉浮层局部有边框，属正常）
    panel_root = panel.split("<template>", 1)[1].split(">", 1)[0]
    assert "border-t border-gray-100" in panel_root
    assert "rounded-xl border border-gray-200" not in panel_root
