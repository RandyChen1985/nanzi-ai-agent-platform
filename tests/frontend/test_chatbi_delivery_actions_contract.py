from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_brief_and_monitor_are_real_local_actions_on_both_surfaces():
    backend = _source("app/services/ai/runners/chatbi/insight_meta.py")
    assert '"brief", "生成业务简报"' in backend
    assert '"monitor", "创建订阅"' in backend
    assert backend.count('action_type="local_action"') >= 2
    for view in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = _source(view)
        assert 'axios.post("/api/portal/chatbi-briefs"' in source
        assert "assistant_report:" in source
        assert 'axios.post("/api/portal/chatbi-monitors"' not in source
        assert "window.confirm" not in source
        assert 'import ChatBIMonitorDialog from "@/components/chatbi/ChatBIMonitorDialog.vue"' in source
        assert "<ChatBIMonitorDialog" in source
        assert "handleChatBIResultAction(action, msg)" in source
        assert ':result-id="msg.chatbiInsight.result_id"' in source
        assert 'result_id: action.result_id' in source

    chooser = _source("frontend/src/components/chatbi/ChatBIContinueAnalysis.vue")
    assert "resultId?: string" in chooser


def test_monitor_dialog_owns_schedule_validation_and_submission():
    dialog = _source("frontend/src/components/chatbi/ChatBIMonitorDialog.vue")
    assert 'import axios from "@/utils/axios"' in dialog
    assert "订阅查询结果" in dialog
    assert "确认订阅" in dialog
    assert "创建监控" not in dialog
    for token in (
        'role="dialog"',
        'aria-modal="true"',
        '"daily"',
        '"weekly"',
        '"monthly"',
        "time_value",
        "weekday",
        "monthday",
        "notify_on_success",
        "submitting",
        "errorMessage",
        'axios.post("/api/portal/chatbi-monitors"',
    ):
        assert token in dialog


def test_alert_migration_and_condition_editor_are_wired():
    migration = _source("db-prod/V100-add-saved-report-alert-conditions.sql")
    editor = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    assert "alert_condition" in migration and "trigger_evidence" in migration
    assert "savedReportSubscriptionForm.alert_condition.type" in editor
    assert "rate_of_change" in editor and "no_data" in editor
