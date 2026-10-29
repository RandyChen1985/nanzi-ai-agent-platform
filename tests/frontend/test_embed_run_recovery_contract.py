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


def _plan(messages, latest, options=None):
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
  {json.dumps(options or {}, ensure_ascii=False)},
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
    """流在途时由 SSE 自己收敛界面，恢复器不得额外拉历史，也不得启动续显。

    `remoteRunActive` 在正常发送时同样为 true，若不设此闸门，每一次普通对话都会
    多出每 3s 一次的 history 请求（并发用户下是纯浪费的后端 QPS）。

    闸门只看「流是否在途」，不看 Stall 提示：流只是静默时前端无从判断「已渲染到哪」
    （SSE 帧不带事件序号），此时启动续显只能重复正文或清掉已渲染内容——见
    `test_embed_replay_single_writer_contract.py`。
    """
    source = _embed_source()
    start = source.index("const scheduleRunRecoverySync = () => {")
    body = source[start : source.index("runRecoveryTimer = setTimeout(async () => {", start)]
    assert "if (streamInFlight) {" in body
    assert "if (activeStreamDraft) scheduleStreamReplay();" in body
    assert (
        "runRecoveryTimer = setTimeout(scheduleRunRecoverySync, RUN_RECOVERY_SYNC_INTERVAL_MS);"
        in body
    )
    assert "!showStalledPrompt.value" not in body, "Stall 提示不得再作为续显开闸条件"


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
    # 输入框的 busy 语义此后又并入 pendingPermissionLocking：确认卡挂起时这一轮已经停下，
    # 只看 isProcessing / remoteRunBusy 会把输入框误判为空闲，用户可以绕过确认卡发新消息。
    assert (
        ':is-processing="isProcessing || remoteRunBusy || recoveryLocking || pendingPermissionLocking"'
        in source
    )
    assert (
        "const quickSendBlocked = () => isProcessing.value || remoteRunBusy.value || recoveryLocking.value || pendingPermissionLocking.value || sendLocked.value;"
        in source
    )
    assert "watch(remoteRunActive, (active) =>" in source
    # 恢复器有两个驱动力：锁真相与「仍有一份未收敛草稿」。后者让 run-status 查询失败 /
    # 任务恰好在挂载窗口内结束时仍有人收敛。
    assert "if (!remoteRunActive.value && !recoveryPending.value) return;" in source
    assert 'if (document.visibilityState !== "visible") return;' in source
    assert "watch(recoveryPending, (pending) =>" in source



# ---- 无草稿 / 分隔符 / 水位线：刷新后仍能收敛到新答案 ----


SYSTEM_SEPARATOR = {
    "role": "system",
    "content": "以上是历史会话，可以重置会话清除",
    "timestamp": "2026-10-22T10:00:05",
}
HISTORY_TAIL = [
    {"role": "user", "trace_id": "t0", "content": "旧提问", "timestamp": "2026-10-22T10:00:00"},
    {
        "role": "agent",
        "trace_id": "t0",
        "content": "旧回答",
        "isHistory": True,
        "timestamp": "2026-10-22T10:00:05",
    },
    SYSTEM_SEPARATOR,
]


def test_reloads_when_history_is_loaded_and_a_newer_turn_exists_on_server():
    """刷新后快照缺失时，末条历史 + 分隔符不能挡掉新终态。

    旧实现取 `messages[last]`（分隔符）判角色，fill/append 都失效后落到 ignore，
    新回答永远不会出现；这里必须整段重拉。
    """
    assert _plan(
        HISTORY_TAIL,
        {
            "trace_id": "t1",
            "summary": "新回答",
            "query": "新提问",
            "created_at": "2026-10-22T10:05:00",
        },
    ) == {"kind": "reload"}


def test_ignores_an_older_record_that_is_not_on_the_loaded_history_page():
    """更早轮次的陈留记录不得触发整段重拉（否则会重复加载/冲掉本地现场）。"""
    assert _plan(
        HISTORY_TAIL,
        {
            "trace_id": "t-old",
            "summary": "更早回答",
            "query": "更早提问",
            "created_at": "2026-10-22T09:00:00",
        },
    ) == {"kind": "ignore"}


def test_ignores_when_latest_record_is_already_the_loaded_tail():
    assert _plan(
        HISTORY_TAIL,
        {
            "trace_id": "t0",
            "summary": "旧回答",
            "query": "旧提问",
            "created_at": "2026-10-22T10:00:05",
        },
    ) == {"kind": "ignore"}


def test_updates_trace_less_draft_by_query_match():
    """快照里的 trace 丢失/被嵌套 trace 覆盖时，用本轮 query 对上是同一轮即可覆盖。"""
    assert _plan(
        [
            *HISTORY_TAIL,
            {"role": "user", "content": "新提问"},
            {"role": "agent", "content": "半截回答", "isThinking": False},
        ],
        {
            "trace_id": "t1",
            "summary": "完整回答",
            "query": "新提问",
            "created_at": "2026-10-22T10:05:00",
        },
    ) == {"kind": "update", "index": 4}


def test_updates_timeline_only_record_for_an_unsettled_draft():
    """summary 为空但有 timeline 的合法轮次也要收敛（旧判据把它当未落库永远忽略）。

    更新时调用方必须保留草稿正文，不能拿空 summary 覆盖。
    """
    assert _plan(
        [
            *HISTORY_TAIL,
            {"role": "user", "content": "新提问"},
            {"role": "agent", "trace_id": "t1", "content": "半截回答", "isThinking": False},
        ],
        {
            "trace_id": "t1",
            "summary": "",
            "query": "新提问",
            "process_timeline": [{"kind": "log", "title": "工具"}],
            "created_at": "2026-10-22T10:05:00",
        },
        {"hasUnsettledDraft": True},
    ) == {"kind": "update", "index": 4}


def test_appends_when_local_tail_is_the_matching_user_turn():
    """本地只剩本轮提问（快照只有 user）时按 query 追加，不重拉、不丢提问。"""
    assert _plan(
        [*HISTORY_TAIL, {"role": "user", "content": "新提问"}],
        {
            "trace_id": "t1",
            "summary": "答复",
            "query": "新提问",
            "created_at": "2026-10-22T10:05:01",
        },
    ) == {"kind": "append"}


# ---- 接线契约：无草稿补建 / 双驱动力 / 硬刷新落盘 / 轻量轮询 ----


def test_embed_chat_can_rebuild_a_draft_from_redis_history_without_a_snapshot():
    source = _embed_source()
    assert "const ensureRecoveryDraft = async () =>" in source
    assert "/api/v1/chat/conversation/${encodeURIComponent(cid)}" in source
    assert 'if (!last || last.role !== "user"' in source
    assert "const placeholder = ref<Message>({" in source
    assert "scheduleStreamReplay();" in source


def test_embed_chat_recovery_has_a_second_driver_for_unsettled_drafts():
    source = _embed_source()
    assert "const recoveryPending = ref(false);" in source
    assert "const recoveryLocking = computed(" in source
    assert "recoveryPending.value =" in source
    assert "watch(recoveryPending, (pending) =>" in source
    assert "if (!remoteRunActive.value && !recoveryPending.value) return;" in source
    assert "void ensureRecoveryDraft();" in source


def test_embed_chat_flushes_the_draft_on_pagehide_and_resumes_on_pageshow():
    """浏览器硬刷新/关闭标签不会走 onUnmounted，visibilitychange 也不保证派发。"""
    source = _embed_source()
    assert 'window.addEventListener("pagehide", onPageHide);' in source
    assert 'window.addEventListener("pageshow", onPageShow);' in source
    assert "if (!event?.persisted) return;" in source
    assert "onPageHide," in source
    assert 'window.removeEventListener("pagehide", handlers.onPageHide);' in source


def test_embed_chat_history_polling_is_lightweight_and_waits_for_first_page():
    source = _embed_source()
    sync_start = source.index("const syncLatestSessionHistory = async")
    sync_body = source[sync_start : source.index("\n  };", sync_start)]
    assert "page_size: 1" in sync_body
    assert "if (isLoadingHistory.value) {" in sync_body
    # reload 之前不得清快照：慢网络下首屏历史可能还没还原快照。
    reload_at = sync_body.index('if (action.kind === "reload") {')
    fetch_at = sync_body.index("await fetchConversationHistory(false);", reload_at)
    assert reload_at < fetch_at
    assert "clearStreamSnapshot(" not in sync_body[reload_at:fetch_at]


def test_embed_chat_replay_stops_only_on_a_confirmed_terminal():
    source = _embed_source()
    assert "data.run_active === false && data.run_available !== false && !activeStreamDraft" in source


def test_embed_chat_unifies_terminal_sync_cleanup():
    source = _embed_source()
    assert "const finishRecoveredTurn = () =>" in source
    start = source.index("const finishRecoveredTurn = () =>")
    body = source[start : source.index("\n};", start)]
    assert "settleRecoveredTimeline();" in body
    assert "activeStreamDraft = null;" in body
    assert "clearStreamReplayTimer();" in body
    assert "clearStreamSnapshot(conversationId.value);" in body
    assert "refreshRecoveryCard();" in body


def test_unsettled_draft_does_not_turn_a_previous_turn_record_into_an_update():
    """在途草稿不能把「上一轮已落库记录」误判成本轮需要收敛。

    旧判据只要 unsettled 就 update 命中的 trace；而命中的往往是上一轮终态。调用方
    随即会清掉本轮草稿、停续显，恢复器再补建草稿，形成每 3s 一次的抖动。
    """
    assert _plan(
        [
            *HISTORY_TAIL,
            {"role": "user", "content": "新提问"},
            {"role": "agent", "trace_id": "t1", "content": "半截回答", "isThinking": False},
        ],
        {
            "trace_id": "t0",
            "summary": "旧回答",
            "query": "旧提问",
            "created_at": "2026-10-22T10:00:05",
        },
        {"hasUnsettledDraft": True},
    ) == {"kind": "ignore"}


def test_embed_chat_only_finishes_recovery_for_the_current_turn():
    """历史收敛命中上一轮时只补内容，不得清掉本轮在途草稿。"""
    source = _embed_source()
    assert "const actionIsForCurrentTurn =" in source
    assert "action.index > lastLocalUserIndex" in source
    assert "if (actionIsForCurrentTurn) {" in source


def test_trace_less_placeholder_is_not_filled_with_an_older_turn_record():
    """占位没有 trace 时，fill 只能用于「记录的 query 与本轮提问一致」的情况。

    否则恢复器会把上一轮回答填进本轮刚创建的占位气泡，随后又被终态覆盖。
    """
    assert _plan(
        [
            *HISTORY_TAIL,
            {"role": "user", "content": "新提问"},
            {"role": "agent", "content": "", "isThinking": True},
        ],
        {
            "trace_id": "t0",
            "summary": "旧回答",
            "query": "旧提问",
            "created_at": "2026-10-22T10:00:05",
        },
        {"hasUnsettledDraft": True},
    ) == {"kind": "ignore"}


def test_repeated_identical_question_is_not_filled_with_the_previous_turn_answer():
    """同一会话重复提问时，上一轮的记录不能按 query 文本被误判成本轮。

    这里刻意让上一轮的助手消息不在本地（只保留提问 + 分隔符），此时 trace 匹配不到，
    只能靠「记录 trace 属于最后一条用户消息之前的某一轮」排除。
    """
    messages = [
        {"role": "user", "trace_id": "t0", "content": "查一下", "timestamp": "2026-10-22T10:00:00"},
        SYSTEM_SEPARATOR,
        {"role": "user", "content": "查一下"},
        {"role": "agent", "content": "", "isThinking": True},
    ]
    assert _plan(
        messages,
        {
            "trace_id": "t0",
            "summary": "上一轮的回答",
            "query": "查一下",
            "created_at": "2026-10-22T10:00:05",
        },
        {"hasUnsettledDraft": True},
    ) == {"kind": "ignore"}


def test_repeated_identical_question_still_converges_its_own_new_record():
    """同文本提问的**本轮**记录（trace 不属于更早的提问）仍要正常补进占位。"""
    messages = [
        {"role": "user", "trace_id": "t0", "content": "查一下", "timestamp": "2026-10-22T10:00:00"},
        SYSTEM_SEPARATOR,
        {"role": "user", "content": "查一下"},
        {"role": "agent", "content": "", "isThinking": True},
    ]
    assert _plan(
        messages,
        {
            "trace_id": "t1",
            "summary": "本轮回答",
            "query": "查一下",
            "created_at": "2026-10-22T10:05:00",
        },
        {"hasUnsettledDraft": True},
    ) == {"kind": "fill", "index": 3}


def test_embed_chat_synced_cleanup_requires_the_answer_after_the_last_user():
    """已同步收尾也必须校验位置：重复提问时上一轮的答案不能触发本轮收尾。"""
    source = _embed_source()
    assert "const latestSyncedIndex =" in source
    assert "latestSyncedIndex > lastLocalUserIndex" in source
    assert "if (latestSyncedIndex !== -1 && isLatestTurn)" in source
