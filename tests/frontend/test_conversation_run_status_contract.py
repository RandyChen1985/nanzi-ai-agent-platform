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
        # 输入区绑定的是「本轮是否还占着输入」的 busy 判定，而不是后端锁真相：
        # 锁真相要在输出完成后继续驱动恢复收敛，不能被输入解锁一并抹掉。
        if relative_path.endswith("EmbedChat.vue"):
            # EmbedChat 额外用 recoveryLocking 兜住「运行态未知但仍有未收敛草稿」的窗口。
            assert ':is-processing="isProcessing || remoteRunBusy || recoveryLocking"' in source
            assert "if (isProcessing.value || remoteRunBusy.value || recoveryLocking.value) return;" in source
        else:
            assert ':is-processing="isProcessing || remoteRunBusy"' in source
            assert "if (isProcessing.value || remoteRunBusy.value) return;" in source
        assert ':is-submitting="sendLocked"' in source


def test_mark_output_completed_keeps_lock_truth_alive_for_recovery():
    """输出完成只应解锁输入，不得把「后端仍在跑」这个事实从界面抹掉。

    后端在 finalize 里先 yield run_status 帧、之后才落库；前端收到该帧（或切回后
    续显重放该帧）就会 markOutputCompleted()。若这一次调用同时把 remoteRunActive
    压成 false，运行恢复器与 run-status 轮询会一起停摆，落库慢于最后一次历史同步时
    界面永久停在草稿、只能刷新页面（用户实测现象）。
    """
    result = _run_typescript(
        """
let active = true;
const controller = api.createConversationRunStatusController(
  async () => ({ active, trace_id: 't1', ttl_seconds: 30 }), 100000);
await controller.refresh('c1');
const lockAfterSync = controller.remoteRunActive.value;
controller.markOutputCompleted();
const lockAfterMark = controller.remoteRunActive.value;
await controller.refresh('c1');
const lockAfterResync = controller.remoteRunActive.value;
active = false;
await controller.refresh('c1');
const lockWhenDone = controller.remoteRunActive.value;
// 必须停掉轮询定时器：否则 node 会带着待触发的 setTimeout 不退出，测试挂死。
controller.stopPolling();
return {
  lockAfterSync, lockAfterMark, lockAfterResync, lockWhenDone,
};
""",
    )
    assert result == {
        "lockAfterSync": True,
        "lockAfterMark": True,
        "lockAfterResync": True,
        "lockWhenDone": False,
    }


def test_composer_busy_flag_is_released_by_completion_without_stopping_recovery():
    """输入解锁走 busy 判定：输出完成即解锁，但锁真相继续驱动恢复。"""
    result = _run_typescript(
        """
let active = true;
const controller = api.createConversationRunStatusController(
  async () => ({ active, trace_id: 't1', ttl_seconds: 30 }), 100000);
await controller.refresh('c1');
const busyAfterSync = controller.remoteRunBusy.value;
controller.markOutputCompleted();
const busyAfterMark = controller.remoteRunBusy.value;
const lockAfterMark = controller.remoteRunActive.value;
active = false;
await controller.refresh('c1');
const busyWhenDone = controller.remoteRunBusy.value;
controller.stopPolling();
return {
  busyAfterSync, busyAfterMark, lockAfterMark, busyWhenDone,
};
""",
    )
    assert result == {
        "busyAfterSync": True,
        "busyAfterMark": False,
        "lockAfterMark": True,
        "busyWhenDone": False,
    }


def test_session_reset_clears_output_completed_so_a_running_turn_relocks_the_composer():
    """会话重置不得留下「输出已完成」标记。

    切页面再切回会在新实例里走一次身份重置。若重置路径复用 markOutputCompleted，
    该标记会残留，之后任何一次「仍有一轮在跑」的查询都会把输入锁判成 false——
    表现为恢复器活着（思考卡片在动）、输入框却始终不锁（用户实测）。
    """
    result = _run_typescript(
        """
let active = true;
const controller = api.createConversationRunStatusController(
  async () => ({ active, trace_id: 't1', ttl_seconds: 30 }), 100000);
controller.markOutputCompleted();
controller.resetRunStatus();
await controller.refresh('c1');
const lockAfterReset = controller.remoteRunActive.value;
const busyAfterReset = controller.remoteRunBusy.value;
controller.stopPolling();
return { lockAfterReset, busyAfterReset };
""",
    )
    assert result == {"lockAfterReset": True, "busyAfterReset": True}


def test_starting_a_new_turn_resets_output_completed():
    """新一轮开始必须复位 outputCompleted。

    否则上一轮的「输出已完成」会持续压制新一轮的锁：用户切走再切回时恢复器
    不启动，界面停在半截输出。
    """
    result = _run_typescript(
        """
let active = true;
const controller = api.createConversationRunStatusController(
  async () => ({ active, trace_id: 't1', ttl_seconds: 30 }), 100000);
await controller.refresh('c1');
controller.markOutputCompleted();
controller.markRunStarted();
const lockAfterStart = controller.remoteRunActive.value;
const busyAfterStart = controller.remoteRunBusy.value;
await controller.refresh('c1');
const lockAfterRefresh = controller.remoteRunActive.value;
const busyAfterRefresh = controller.remoteRunBusy.value;
controller.stopPolling();
return {
  lockAfterStart, busyAfterStart, lockAfterRefresh, busyAfterRefresh,
};
""",
    )
    assert result == {
        "lockAfterStart": True,
        "busyAfterStart": True,
        "lockAfterRefresh": True,
        "busyAfterRefresh": True,
    }


def test_embed_chat_focuses_desktop_input_only_after_all_busy_states_clear():
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")

    assert "const focusChatInputWhenReady = () =>" in source
    assert "if (isMobile.value || isProcessing.value || remoteRunBusy.value || recoveryLocking.value || sendLocked.value) return;" in source
    assert "watch([isProcessing, remoteRunBusy, sendLocked], focusChatInputWhenReady);" in source
    assert "nextTick(() => chatInputRef.value?.focus());" in source

    completion_block = source[source.index("isProcessing.value = false;", source.index("const sendMessageInternal")):source.index("const BOTTOM_THRESHOLD_PX")]
    assert "chatInputRef.value?.focus()" not in completion_block


def test_agent_debug_focuses_desktop_input_only_after_all_busy_states_clear():
    source = (ROOT / "frontend/src/views/AgentDebug.vue").read_text(encoding="utf-8")

    watch_index = source.index("watch([isProcessing, remoteRunBusy, sendLocked], focusChatInputWhenReady);")
    assert source.index("const isProcessing = ref(false);") < watch_index
    assert source.index("const { locked: sendLocked", source.index("const isProcessing = ref(false);")) < watch_index
    assert "const focusChatInputWhenReady = () =>" in source
    assert "if (isMobile.value || isProcessing.value || remoteRunBusy.value || sendLocked.value) return;" in source
    assert "watch([isProcessing, remoteRunBusy, sendLocked], focusChatInputWhenReady);" in source
    assert "nextTick(() => chatInputRef.value?.focus());" in source

    # 收尾不再直接置 false，而是在 finally 中按待处理权限/外部执行状态收敛 isProcessing；
    # 焦点仍只由上面的 watch 统一处理，收尾路径不得直接 focus。
    completion_start = source.index(
        "isProcessing.value = agentMsg.value.pendingPermission",
        source.index("const sendMessageInternal"),
    )
    completion_block = source[completion_start:source.index("const addRealLog")]
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


def test_failed_status_query_keeps_the_last_lock_state_and_retries():
    """查询失败 ≠ 运行结束：必须保留上次锁态并继续重试。

    旧实现 catch 后直接把 remoteRunActive/remoteRunBusy 压成 false 并停掉轮询：
    一次网络抖动就会让恢复器与输入锁一起消失，而服务端 producer 仍在跑。
    """
    result = _run_typescript(
        """
let calls = 0;
const controller = api.createConversationRunStatusController(async () => {
  calls += 1;
  if (calls === 1) return { active: true, trace_id: 't1', ttl_seconds: 30, available: true };
  throw new Error('network down');
}, 100000);
await controller.refresh('c1');
const activeAfterOk = controller.remoteRunActive.value;
const busyAfterOk = controller.remoteRunBusy.value;
const unknownAfterOk = controller.statusUnknown.value;
await controller.refresh('c1');
const activeAfterFail = controller.remoteRunActive.value;
const busyAfterFail = controller.remoteRunBusy.value;
const unknownAfterFail = controller.statusUnknown.value;
const pollingAfterFail = controller.isPolling();
controller.stopPolling();
return { activeAfterOk, busyAfterOk, unknownAfterOk, activeAfterFail, busyAfterFail, unknownAfterFail, pollingAfterFail };
"""
    )
    assert result == {
        "activeAfterOk": True,
        "busyAfterOk": True,
        "unknownAfterOk": False,
        "activeAfterFail": True,
        "busyAfterFail": True,
        "unknownAfterFail": True,
        "pollingAfterFail": True,
    }


def test_unavailable_payload_is_treated_as_unknown_instead_of_finished():
    """后端 hash 不可用（available=false）时不能当成「已结束」解锁输入。"""
    result = _run_typescript(
        """
const controller = api.createConversationRunStatusController(
  async () => ({ active: false, trace_id: null, ttl_seconds: null, available: false }),
  100000,
);
await controller.refresh('c1');
const unknown = controller.statusUnknown.value;
const active = controller.remoteRunActive.value;
const polling = controller.isPolling();
controller.stopPolling();
return { unknown, active, polling };
"""
    )
    assert result == {"unknown": True, "active": False, "polling": True}


def test_a_real_inactive_answer_clears_unknown_and_stops_polling():
    result = _run_typescript(
        """
const controller = api.createConversationRunStatusController(
  async () => ({ active: false, trace_id: null, ttl_seconds: null, available: true }),
  100000,
);
await controller.refresh('c1');
const unknown = controller.statusUnknown.value;
const polling = controller.isPolling();
controller.stopPolling();
return { unknown, polling };
"""
    )
    assert result == {"unknown": False, "polling": False}


def test_embed_chat_turns_error_envelopes_into_unknown_run_status():
    """HTTP 200 + 错误包络同样不可信：必须返回 available=false 让控制器重试。"""
    source = (ROOT / "frontend/src/views/EmbedChat.vue").read_text(encoding="utf-8")
    run_status_start = source.index("} = useConversationRunStatus(async (cid) => {")
    block = source[run_status_start : run_status_start + 1200]
    assert "if (!isApiSuccess(response.data))" in block
    assert "available: false" in block
