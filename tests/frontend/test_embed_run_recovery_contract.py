"""契约：EmbedChat 运行恢复器（run-status 驱动的会话续订）。

覆盖两层：
1. `planHistorySync` 纯函数行为（node 转译真实 TS 源码执行，非复刻）。
2. `EmbedChat.vue` 的接线契约（周期同步、终态收敛、busy 权威复位、卸载 abort）。

背景：`/embed/chat` 与 `/dashboard/users` 是顶层路由且无 keep-alive，切走再切回
会把 EmbedChat 卸载重建；`document.visibilityState` 不变，`visibilitychange` 不触发，
既有恢复链完全不执行。服务端 producer 仍会把任务跑完落库，因此必须由前端按
`run-status` 主动回收结果。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
pytestmark = pytest.mark.no_infrastructure


def _plan(messages, latest):
    module_path = "frontend/src/utils/runRecoverySync.ts"
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const result = moduleRef.exports.planHistorySync(
  {json.dumps(messages, ensure_ascii=False)},
  {json.dumps(latest, ensure_ascii=False)},
);
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def test_updates_matching_trace_when_server_summary_is_longer():
    assert _plan(
        [
            {"role": "user", "trace_id": "t1", "content": "hi"},
            {"role": "agent", "trace_id": "t1", "content": "半", "isThinking": False},
        ],
        {"trace_id": "t1", "summary": "完整回答"},
    ) == {"kind": "update", "index": 1}


def test_ignores_matching_trace_when_frontend_already_has_full_content():
    assert _plan(
        [{"role": "agent", "trace_id": "t1", "content": "完整回答", "isThinking": False}],
        {"trace_id": "t1", "summary": "完整回答"},
    ) == {"kind": "ignore"}


def test_fills_trace_less_streaming_placeholder_instead_of_duplicating():
    assert _plan(
        [
            {"role": "user", "trace_id": "t1", "content": "hi"},
            {"role": "agent", "content": "", "isThinking": True},
        ],
        {"trace_id": "t1", "summary": "后台跑完的回答"},
    ) == {"kind": "fill", "index": 1}


def test_appends_when_only_the_user_turn_is_present():
    """本轮 user 已落库（历史条目 user/assistant 共用 trace_id），助手结果到达时追加。"""
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "hi"}],
        {"trace_id": "t1", "summary": "后台跑完的回答"},
    ) == {"kind": "append"}


def test_appends_when_local_user_message_has_no_trace_id_yet():
    """本地刚发出的 user 消息还没回填 trace_id 时同样追加，不丢回答。"""
    assert _plan(
        [{"role": "user", "content": "hi"}],
        {"trace_id": "t1", "summary": "后台跑完的回答"},
    ) == {"kind": "append"}


def test_ignores_stale_record_belonging_to_an_older_turn():
    """最新记录属于更早的轮次时不得追加，避免把旧回答贴到新提问下面。"""
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "新提问"}],
        {"trace_id": "t0", "summary": "旧回答"},
    ) == {"kind": "ignore"}


def test_reloads_full_history_when_message_list_is_empty():
    """页面重建后消息列表为空且服务端已有终态记录：必须重拉整段历史。

    只 append 一条助手消息会丢掉对应的用户提问；若直接忽略（旧行为），
    切回后即使任务跑完回答也永远不会出现在界面上。
    """
    assert _plan([], {"trace_id": "t1", "summary": "后台跑完的回答"}) == {"kind": "reload"}


def test_does_not_reload_when_nothing_is_persisted_yet():
    assert _plan([], {"trace_id": "t1", "summary": ""}) == {"kind": "ignore"}


def test_ignores_when_latest_record_has_no_persisted_summary():
    assert _plan(
        [{"role": "user", "trace_id": "t1", "content": "hi"}],
        {"trace_id": "t1", "summary": ""},
    ) == {"kind": "ignore"}


def test_embed_chat_routes_history_sync_through_decision_helper():
    source = _embed_source()
    assert 'from "@/utils/runRecoverySync"' in source
    assert "planHistorySync(" in source
    assert "action.kind ===" in source


def test_embed_chat_runs_status_driven_recovery_sync():
    source = _embed_source()
    assert "let streamInFlight = false;" in source
    assert "const RUN_RECOVERY_SYNC_INTERVAL_MS = 3000;" in source
    assert "const scheduleRunRecoverySync = ()" in source
    assert "watch(remoteRunActive, (active) =>" in source
    assert "void syncLatestSessionHistory(1, 5);" in source
    # busy 复位必须以 run-status 为准，且不能在有流在途时误复位
    assert "if (!streamInFlight && isProcessing.value && !hasPendingConfirmation())" in source


def test_embed_chat_starts_recovery_when_run_status_resolved_before_mount():
    """挂载竞态兜底：run-status 早于本组件挂载解析为 active 时仍要启动恢复同步。

    锚点必须带缩进：`scheduleRunRecoverySync` 的 setTimeout 回调里有一行同样的调用，
    只按裸字符串断言会变成永真式（删掉兜底也照样通过）。
    """
    source = _embed_source()
    assert "// 挂载竞态兜底" in source
    assert "\n  if (remoteRunActive.value) scheduleRunRecoverySync();" in source


def test_embed_chat_reloads_full_history_when_message_list_is_empty():
    """组件必须处理 reload 分支：空列表场景要重拉整段历史，而不是忽略。"""
    source = _embed_source()
    assert 'if (action.kind === "reload") {' in source
    assert "await fetchConversationHistory(false);" in source


def test_embed_chat_skips_redundant_history_polling_while_stream_is_healthy():
    """流在正常推进时由 SSE 自己收敛界面，恢复器不得额外拉历史。

    `remoteRunActive` 在正常发送时同样为 true，若不设此闸门，每一次普通对话都会
    多出每 3s 一次的 history 请求（并发用户下是纯浪费的后端 QPS）。只有流已静默
    （复用既有 Stall 信号）或根本没有流在途（页面卸载重建）才需要恢复同步。
    """
    source = _embed_source()
    assert "if (streamInFlight && !showStalledPrompt.value) {" in source
    assert (
        "runRecoveryTimer = setTimeout(scheduleRunRecoverySync, RUN_RECOVERY_SYNC_INTERVAL_MS);"
        in source
    )


def test_embed_chat_clears_recovery_timer_on_hide_and_unmount():
    source = _embed_source()
    assert "clearRunRecoveryTimer();" in source
    assert "handlers?.clearRunRecoveryTimer" in source


def test_embed_chat_aborts_in_flight_stream_on_unmount():
    source = _embed_source()
    assert "embedUnmounted = true;" in source
    assert "if (abortController) abortController.abort();" in source
    # 卸载后的收尾副作用必须被守卫
    assert "if (!embedUnmounted) {" in source


def _keep_recovery_sync(is_processing: bool, last_message, has_unsettled_draft: bool):
    """直接执行真实 TS 源码里的重试判据（非复刻）。"""
    module_path = "frontend/src/utils/runRecoverySync.ts"
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(module_path)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const result = moduleRef.exports.shouldKeepRecoverySync({{
  isProcessing: {json.dumps(is_processing)},
  lastMessage: {json.dumps(last_message, ensure_ascii=False)},
  hasUnsettledDraft: {json.dumps(has_unsettled_draft)},
}});
process.stdout.write(JSON.stringify(result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_recovery_retry_continues_while_a_draft_is_still_unsettled():
    """在途草稿（快照已还原、落库终态未到）时不得因为「草稿已有正文」而放弃重试。

    切回时草稿是「切走瞬间」的前缀，落库终态可能还要几秒到几十秒才出现。旧判据只看
    `isThinking || !content`，草稿一旦有正文就判定 needsSync=false 并静默放弃重试，
    于是界面永久停在这份草稿上——这正是「刷新页面才能看到已跑完的回答」的成因。
    """
    assert (
        _keep_recovery_sync(
            False, {"role": "agent", "content": "半截回答", "isThinking": False}, True
        )
        is True
    )


def test_recovery_retry_stops_when_nothing_is_pending():
    assert (
        _keep_recovery_sync(
            False, {"role": "agent", "content": "完整回答", "isThinking": False}, False
        )
        is False
    )


def test_recovery_retry_covers_processing_and_placeholder_states():
    assert _keep_recovery_sync(True, None, False) is True
    assert _keep_recovery_sync(False, {"role": "user", "content": "hi"}, False) is True
    assert (
        _keep_recovery_sync(
            False, {"role": "agent", "content": "", "isThinking": True}, False
        )
        is True
    )


def test_embed_chat_routes_retry_decision_through_pure_helper():
    source = _embed_source()
    assert "shouldKeepRecoverySync({" in source
    assert "hasUnsettledDraft: activeStreamDraft !== null," in source


def test_session_reset_clears_run_status_instead_of_marking_output_completed():
    """身份/会话重置必须整体复位运行态，不能只标记「本轮输出已完成」。

    切回页面会走一次重置；若重置只调 markOutputCompleted()，残留的完成标记会让
    随后「仍有一轮在跑」的查询无法重新锁住输入框。
    """
    source = _embed_source()
    assert "stopRemoteRunPolling();\n  markOutputCompleted();" not in source
    assert "stopRemoteRunPolling();\n  resetRunStatus();" in source


def test_stream_replay_is_idempotent_against_a_stale_cursor():
    """续显幂等不能只信游标，必须以「草稿已应用进度」兜底。

    游标会随快照恢复 / 会话重置归零；一旦它落后于草稿的真实进度，同一批事件会被
    再叠加一遍——表现为同一条回复里整段正文重复（用户实测）。
    """
    source = _embed_source()
    assert "lastAppliedSeq?: number" in source
    assert "const draftAppliedSeq = Math.max(0, Number(activeStreamDraft?.lastAppliedSeq ?? 0) || 0);" in source
    assert "const afterSeq = Math.max(Number(streamReplayCursor.value.lastSeq || 0) || 0, draftAppliedSeq);" in source
    assert "params: { after_seq: afterSeq }," in source
    assert "activeStreamDraft.lastAppliedSeq = Math.max(draftAppliedSeq, Number(plan.lastSeq || 0) || 0);" in source
    # 首次判定同样要用有效起点，否则会把「草稿进度领先」误判成首次而清空正文。
    assert "const isFirstPoll = shouldResetDraftBeforeReplay(\n        { lastSeq: afterSeq }," in source


def test_recovery_poll_does_not_trip_the_give_up_fallback():
    """恢复器的周期轮询不得落进「重试耗尽」的兜底分支。

    恢复器按 (1, 1) 调用：它本身靠外层定时器反复重试，不存在「重试耗尽」。若兜底
    分支对它也生效，就会每 3s 调一次 markOutputCompleted()，把仍被后端锁住的输入框
    反复解锁，并留下「输出已完成」标记污染后续加锁。
    """
    source = _embed_source()
    assert "await syncLatestSessionHistory(1, 1);" in source
    assert "} else if (maxAttempts > 1) {" in source


def test_embed_chat_stream_replay_does_not_finish_the_turn():
    """切回后的历史重放只做过程展示，不得触发「本轮结束」的交互收尾。

    事件日志里含 run_status 终态帧（后端先发该帧、之后才落库，且它不在合并白名单里），
    续显必然重放它。若重放也调用 markOutputCompleted()，恢复器会在启动后 1 秒内
    自断：这正是「没有 toast、输入框没锁、界面停在半截」的直接成因。
    """
    source = _embed_source()

    replay_start = source.index("const pollConversationStreamEvents")
    replay_end = source.index("const scheduleStreamReplay")
    replay_block = source[replay_start:replay_end]
    assert "applyPermissionStreamEvent(replayTarget, event, { replay: true });" in replay_block

    apply_start = source.index("const applyPermissionStreamEvent")
    apply_block = source[apply_start : apply_start + 3000]
    assert "const replay = options?.replay === true;" in apply_block

    run_status_branch = apply_block[apply_block.index('type === "run_status"') :]
    guarded_at = run_status_branch.index("if (!replay) {")
    assert "markOutputCompleted();" in run_status_branch[guarded_at : guarded_at + 200]
    assert "clearStallTimer();" in run_status_branch[guarded_at : guarded_at + 200]


def test_embed_chat_keeps_lock_truth_driving_recovery_while_composer_uses_busy():
    """恢复器必须以锁真相为驱动，输入解锁只认 busy：两者不得再耦合成同一个标志。"""
    source = _embed_source()
    assert ':is-processing="isProcessing || remoteRunBusy"' in source
    assert "const quickSendBlocked = () => isProcessing.value || remoteRunBusy.value || sendLocked.value;" in source
    assert "watch(remoteRunActive, (active) =>" in source
    assert "if (!remoteRunActive.value || document.visibilityState" in source

