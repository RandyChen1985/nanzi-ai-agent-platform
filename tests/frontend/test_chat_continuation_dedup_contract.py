from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def test_continuation_actions_have_function_level_submission_guards():
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        for function_name, marker in (
            ("submitPendingExternalExecution", "pendingExternalExecution"),
            ("confirmPendingPermission", "pendingPermission"),
        ):
            start = source.index(f"const {function_name} = async")
            body = source[start:]
            assert "pending.isSubmitting" in body, (relative_path, function_name)
            assert "|| pending.isSubmitting" in body, (relative_path, function_name)
            assert body.index("pending.isSubmitting") < body.index("await "), (relative_path, function_name)
            assert marker in body


def test_permission_resume_keeps_a_followup_request_pending():
    """第二次工具审批的 awaiting_permission 终态不能覆盖新卡片的 pending 状态。"""
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        start = source.index('if (data?.type === "run_status")')
        body = source[start : start + 1400]
        assert 'data.status === "awaiting_permission"' in body, relative_path
        assert '"pending"' in body, relative_path


def test_external_resume_keeps_a_followup_request_pending():
    """第二次外部执行的 awaiting_external_execution 终态不能把新卡判成已完成。

    否则卡片显示「已完成」、提交区消失（v-if status==='pending'），用户再也提交不了，
    而后端会一直挂到挂起快照 TTL 到期。
    """
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        start = source.index('if (data?.type === "run_status")')
        body = source[start : start + 2000]
        external = body[body.index("msg.pendingExternalExecution") :]
        assert 'data.status === "awaiting_external_execution"' in external, relative_path
        assert '"pending"' in external, relative_path


def test_initial_stream_keeps_composer_busy_while_continuation_is_pending():
    """首段流结束时，待确认的后续操作仍必须占用当前会话。"""
    expected = (
        'isProcessing.value = agentMsg.value.pendingPermission?.status === "pending" '
        '|| agentMsg.value.pendingExternalExecution?.status === "pending";'
    )
    boundaries = {
        "frontend/src/views/EmbedChat.vue": "const BOTTOM_THRESHOLD_PX",
        "frontend/src/views/AgentDebug.vue": "const addRealLog",
    }
    for relative_path, boundary in boundaries.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        start = source.index("const sendMessageInternal = async")
        body = source[start : source.index(boundary, start)]
        assert expected in body, relative_path
