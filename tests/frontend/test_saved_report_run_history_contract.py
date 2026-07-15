from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure


COMPONENT = Path(__file__).resolve().parents[2] / "frontend/src/components/chatbi/DatasetCapabilityMenu.vue"


def test_saved_report_detail_has_run_history_tab_and_lazy_endpoints():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "报表信息" in source
    assert "运行历史" in source
    assert "savedReportDetailTab" in source
    assert "fetchSavedReportRuns" in source
    assert "`/api/portal/saved-reports/${reportId}/runs`" in source
    assert "`/api/portal/saved-reports/${reportId}/runs/${runId}`" in source


def test_saved_report_run_history_exposes_core_states():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "savedReportRunsLoading" in source
    assert "暂无运行记录" in source
    assert "result_snapshot" in source
    assert "error_message" in source
