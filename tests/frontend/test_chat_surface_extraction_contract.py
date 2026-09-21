from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_debug_uses_extracted_logic_flow_modal_without_losing_diagram_content():
    debug = _read("frontend/src/views/AgentDebug.vue")
    modal_path = ROOT / "frontend/src/components/debug/AgentLogicFlowModal.vue"

    assert modal_path.exists()
    modal = modal_path.read_text(encoding="utf-8")

    # 236d53a1 移除 AgentDebug 顶部「运行逻辑」入口后，该弹窗不再被挂载
    # （见 test_chat_surface_refactor_contract.test_agent_debug_removes_redundant_top_mode_selector_...）；
    # 组件文件仍保留完整流程图内容，继续保护其信息不被清空。
    assert "AgentLogicFlowModal" not in debug
    assert "<!-- Modal: Logic Flow SVG -->" not in debug
    assert "visible: boolean" in modal
    assert '"close"' in modal
    for label in ("用户提问", "意图识别", "Agent Service", "结果合成渲染", "审计"):
        assert label in modal


def test_both_chat_surfaces_use_shared_model_call_stats_modal():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")
    modal_path = ROOT / "frontend/src/components/chat/ChatModelCallStatsModal.vue"

    assert modal_path.exists()
    modal = modal_path.read_text(encoding="utf-8")

    for source in (embed, debug):
        assert "<ChatModelCallStatsModal" in source
        assert "<!-- Model Call Stats Modal -->" not in source
    assert "visible: boolean" in modal
    assert "loading: boolean" in modal
    assert "stats: any[]" in modal
    assert '"close"' in modal
    assert '"toggle"' in modal
    assert "大模型调用明细指标" in modal


def test_both_chat_surfaces_use_shared_saved_report_dialogs():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert (ROOT / "frontend/src/components/data-portal/DataPortalReportCreateModal.vue").exists()
    assert (ROOT / "frontend/src/components/chat/SavedReportRunModal.vue").exists()

    for source in (embed, debug):
        assert "<DataPortalReportCreateModal" in source
        assert "<SavedReportRunModal" in source
        assert "<SavedReportEditorModal" not in source
        assert "<!-- Modal: Save Report -->" not in source
        assert "<!-- Modal: Run Saved Report -->" not in source

    runner = _read("frontend/src/components/chat/SavedReportRunModal.vue")
    assert "pendingReport: any" in runner
    assert '"execute"' in runner
    assert '@click.self="emit(\'close\')"' in runner
    assert "实际执行 SQL" in runner
