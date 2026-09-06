import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _run_typescript(expression: str):
    module_path = "frontend/src/composables/chat/useConversationRunStatus.ts"
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
const requireModule = id => id === 'vue'
  ? {{ ref: value => ({{ value }}), onUnmounted: () => {{}} }}
  : require(id);
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, requireModule);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_run_status_controller_ignores_stale_conversation_results_and_cleans_up():
    result = _run_typescript(
        """
let resolveOld;
let resolveNew;
const fetchStatus = id => new Promise(resolve => {
  if (id === 'old') resolveOld = resolve;
  else resolveNew = resolve;
});
const controller = api.createConversationRunStatusController(fetchStatus, 100000);
const oldRequest = controller.refresh('old');
const newRequest = controller.refresh('new');
resolveOld({ active: true, trace_id: 'old-trace', ttl_seconds: 20 });
await oldRequest;
const afterOld = controller.remoteRunActive.value;
resolveNew({ active: false, trace_id: null, ttl_seconds: null });
await newRequest;
const afterNew = controller.remoteRunActive.value;
controller.startPolling('new');
controller.stopPolling();
return { afterOld, afterNew, stopped: controller.isPolling() === false };
""",
    )
    assert result == {"afterOld": False, "afterNew": False, "stopped": True}


def test_both_chat_surfaces_refresh_remote_status_on_visibility_and_bind_busy_states():
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "useConversationRunStatus" in source
        assert "run-status" in source
        assert "refreshCurrentRunStatus" in source
        assert "visibilitychange" in source
        assert ':is-processing="isProcessing || remoteRunActive"' in source
        assert ':is-submitting="sendLocked"' in source
        assert "if (isProcessing.value || remoteRunActive.value) return;" in source


def test_embed_chat_focuses_desktop_input_only_after_all_busy_states_clear():
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "const focusChatInputWhenReady = () =>" in source
    assert "if (isMobile.value || isProcessing.value || remoteRunActive.value || sendLocked.value) return;" in source
    assert "watch([isProcessing, remoteRunActive, sendLocked], focusChatInputWhenReady);" in source
    assert "nextTick(() => chatInputRef.value?.focus());" in source

    completion_block = source[source.index("isProcessing.value = false;", source.index("const sendMessageInternal")):source.index("const BOTTOM_THRESHOLD_PX")]
    assert "chatInputRef.value?.focus()" not in completion_block


def test_agent_debug_focuses_desktop_input_only_after_all_busy_states_clear():
    source = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    watch_index = source.index("watch([isProcessing, remoteRunActive, sendLocked], focusChatInputWhenReady);")
    assert source.index("const isProcessing = ref(false);") < watch_index
    assert source.index("const { locked: sendLocked", source.index("const isProcessing = ref(false);")) < watch_index
    assert "const focusChatInputWhenReady = () =>" in source
    assert "if (isMobile.value || isProcessing.value || remoteRunActive.value || sendLocked.value) return;" in source
    assert "watch([isProcessing, remoteRunActive, sendLocked], focusChatInputWhenReady);" in source
    assert "nextTick(() => chatInputRef.value?.focus());" in source

    completion_block = source[source.index("isProcessing.value = false;", source.index("const sendMessageInternal")):source.index("const addRealLog")]
    assert "chatInputRef.value?.focus()" not in completion_block


def test_agent_debug_preserves_dataset_name_when_editing_saved_report():
    source = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")
    edit_start = source.index("const openEditReportModal")
    edit_end = source.index("const closeSavedReportEditor", edit_start)

    assert "dataset_name: report.dataset_name || ''" in source[edit_start:edit_end]


def test_context_compaction_control_is_user_triggered_on_both_chat_surfaces():
    input_source = (ROOT / "frontend/src/components/embed/ChatInput.vue").read_text(encoding="utf-8")
    assert "manual-context-compaction" in input_source
    assert "立即压缩上下文" in input_source
    assert "context-compaction-manual" in input_source
    assert "contextCompactionRetainRatio" in input_source
    assert "轻度 75%" in input_source
    assert "标准 50%" in input_source
    assert "深度 25%" in input_source

    for relative_path in ("frontend/src/views/EmbedChat.vue", "frontend/src/views/AgentDebug.vue"):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "@manual-context-compaction" in source
        assert "manualCompact" in source
        assert "contextCompactionActionLoading" in source
        manual_start = source.index("const manualCompact")
        manual_end = source.index("};", manual_start) + 2
        assert "refreshContextUsage" in source[manual_start:manual_end]

    api_source = (ROOT / "frontend/src/api/agent.ts").read_text(encoding="utf-8")
    assert "context_compactions/manual" in api_source


def test_resume_flow_run_status_triggers_completion_finalization_on_both_surfaces():
    """恢复流（权限确认 / 外部执行）末尾的 run_status 不得被静默丢弃。

    主流 sendMessage 已有 run_status 完成收尾；恢复流事件走 applyPermissionStreamEvent，
    该函数此前未处理 run_status，导致恢复后 Stall 计时与“进行中/已完成”文案失收（Bug-17）。
    必须在两个聊天表面的 applyPermissionStreamEvent 内补齐完成收尾。
    """
    for relative_path in (
        "frontend/src/views/EmbedChat.vue",
        "frontend/src/views/AgentDebug.vue",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        fn_start = source.index("const applyPermissionStreamEvent")
        region = source[fn_start : fn_start + 3000]
        assert 'type === "run_status"' in region, f"{relative_path} 恢复流未处理 run_status"
        assert "markOutputCompleted()" in region, f"{relative_path} 恢复流未做完成收尾"
        assert 'data.status === "success"' in region, f"{relative_path} 恢复流未按 success 状态收尾"
