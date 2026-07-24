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

    assert "export interface ChatBIInsightMeta" in types
    assert "actions: ChatBIInsightAction[]" in types
    assert "applyChatBIInsightEvent" in reducer
    assert "chatbi_insight_meta" in reducer
    for source in (embed, debug):
        assert "ChatBIDataEvidence" in source
        assert "ChatBIContinueAnalysis" in source
        assert "chatbiInsight?: ChatBIInsightMeta" in source
        assert "applyChatBIInsightEvent" in source


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
