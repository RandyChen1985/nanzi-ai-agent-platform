"""契约：Embed 会话恢复提示卡片（状态决策 + 接线）。

覆盖两层：
1. `planRecoveryCard` 纯函数行为（node 转译真实 TS 源码执行，非复刻）。
2. `EmbedChat.vue` 与卡片的接线契约（卡片取代 toast、关闭范围、诊断日志清理）。

背景：切页面再切回时，恢复提示此前只有一条 toast，装不下「后台在不在跑、跑到哪、
要不要刷新」；且快照不可用时 `activeStreamDraft` 为 null，`scheduleStreamReplay()`
不会被调用，连那条 toast 都不会出现——后台照跑、界面不动、用户只能手动刷新。
"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
RECOVERY_CARD = ROOT / "frontend/src/components/chat/ChatRecoveryCard.vue"
pytestmark = pytest.mark.no_infrastructure


def _plan(**overrides):
    payload = {
        "runActive": False,
        "hasDraft": False,
        "draftStepCount": 0,
        "syncedEventCount": 0,
        "turnDismissed": False,
        "terminalApplied": False,
        "streaming": False,
        "engaged": False,
    }
    payload.update(overrides)
    module_path = "frontend/src/utils/recoveryCard.ts"
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
const result = moduleRef.exports.planRecoveryCard(
  {json.dumps(payload, ensure_ascii=False)},
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


def test_hidden_when_nothing_is_pending():
    assert _plan()["phase"] == "hidden"


def test_prompts_even_when_no_draft_was_restored():
    """草稿没还原成功时也必须提示。

    这是此前唯二完全静默的场景：恢复器仍在每 3s 收敛，但界面毫无动静。
    """
    result = _plan(runActive=True)
    assert result["phase"] == "syncing"
    assert result["message"] == "正在同步后台进度，输出完成后会自动补齐"


def test_hidden_while_the_local_stream_is_still_healthy():
    """正常对话期间不得误弹恢复提示。

    流健康时 `runActive` 与在途草稿同时为真，但那说明本端正在实时接收，而不是
    「后台在跑、前端断了」的恢复场景。
    """
    assert _plan(runActive=True, hasDraft=True, streaming=True)["phase"] == "hidden"
    assert _plan(runActive=True, streaming=True)["phase"] == "hidden"


def test_draft_takeover_uses_the_existing_copy():
    result = _plan(runActive=True, hasDraft=True)
    assert result["phase"] == "syncing"
    assert result["message"] == "会话仍在后台处理中，已恢复实时同步"


def test_stays_hidden_for_the_rest_of_the_turn_after_user_dismisses():
    """手动关闭的范围是「本轮」：不再打扰，但下一轮会照常提示。"""
    assert _plan(runActive=True, hasDraft=True, turnDismissed=True)["phase"] == "hidden"
    # 关闭优先于完成态：用户已经明确表示不想看这张卡片了。
    assert _plan(terminalApplied=True, turnDismissed=True)["phase"] == "hidden"


def test_shows_done_once_the_terminal_result_landed():
    result = _plan(terminalApplied=True, engaged=True)
    assert result["phase"] == "done"
    assert result["message"] == "执行已完成，内容已补齐"


def test_done_is_silent_when_the_card_never_appeared_this_turn():
    """正常对话（全程没离开页面）结束时不该弹「内容已补齐」。"""
    assert _plan(terminalApplied=True)["phase"] == "hidden"
    assert _plan(terminalApplied=True, runActive=True)["phase"] == "hidden"


def test_done_wins_over_a_still_held_run_lock():
    """内容已补齐就是完成态，即使后端锁还没释放。"""
    assert _plan(runActive=True, terminalApplied=True, engaged=True)["phase"] == "done"


def test_hidden_when_the_user_stopped_this_turn():
    """用户主动停止不是「恢复」场景，两个阶段都不得提示。

    `stopGeneration()` 只 abort 本地 SSE：流不在途、草稿还在、后端锁尚未释放——
    这三条判据与「切走页面后 SSE 静默断开」完全同构。缺了「本端用户主动停止」这个
    输入，界面就会先弹「会话仍在后台处理中」，待取消轮落库后再补一句「执行已完成，
    内容已补齐」，与用户刚按下的停止语义正面冲突。
    """
    assert _plan(runActive=True, hasDraft=True, turnStoppedByUser=True)["phase"] == "hidden"
    assert _plan(terminalApplied=True, engaged=True, turnStoppedByUser=True)["phase"] == "hidden"


def test_collapses_when_the_run_ends_without_a_terminal_write():
    assert _plan(runActive=False, hasDraft=True)["phase"] == "hidden"


def test_detail_reports_stage_steps_and_synced_events():
    result = _plan(
        runActive=True,
        hasDraft=True,
        draftStepCount=18,
        syncedEventCount=12,
        stageLabel="模型调用 · 正在生成回答",
    )
    assert result["detail"] == "模型调用 · 正在生成回答 · 18 步 · 本轮补回 12 条事件"


def test_detail_omits_unknown_pieces_instead_of_printing_zeros():
    no_info = _plan(runActive=True, hasDraft=True)
    assert no_info.get("detail") is None
    only_progress = _plan(runActive=True, syncedEventCount=7)
    assert only_progress["detail"] == "本轮补回 7 条事件"


def test_recovery_card_component_renders_from_the_decision_only():
    source = RECOVERY_CARD.read_text(encoding="utf-8")
    assert "phase === 'syncing'" in source
    assert "phase === 'done'" in source
    assert "message" in source
    # 组件不做决策：不引入恢复链路的状态。
    assert "remoteRunActive" not in source
    assert "activeStreamDraft" not in source
    assert "RecoveryCardState" in source


def test_embed_chat_replaces_the_recovery_toast_with_the_card():
    source = _embed_source()
    assert 'showToast("会话仍在处理中，已恢复实时同步", "info")' not in source
    assert "planRecoveryCard(" in source
    assert "ChatRecoveryCard" in source


def test_embed_chat_clears_diagnostic_logging():
    source = _embed_source()
    assert "[Recovery]" not in source
    assert 'logRecoveryDiag' not in source
    assert 'console.log("[Replay]"' not in source


def test_embed_chat_resets_the_card_on_a_new_turn():
    source = _embed_source()
    assert "refreshRecoveryCard(" in source
    assert "recoveryCardDoneTimer" in source
    # 新一轮提问必须复位「本轮已关闭」标记，否则提示会被永久静默掉。
    assert "recoveryCardDismissedForTurn.value = false" in source


def test_embed_chat_marks_a_user_stopped_turn_so_recovery_stays_silent():
    """停止按钮必须把「本端用户主动停止」送进决策，而不是靠状态巧合。

    abort 只会让 `streamInFlight` 归零：草稿仍在（`activeStreamDraft` 只在 [DONE]
    分支清）、后端锁也还没被 cancel 释放——这正好凑齐「后台在跑、前端断了」的判据。
    所以停止动作自身要置位，并在同一处重算一次卡片（`finally` 里的那次刷新紧随其后）。
    """
    source = _embed_source()
    assert "turnStoppedByUser: recoveryCardStoppedByUser.value" in source
    stop_start = source.index("const stopGeneration = (options?: StopGenerationOptions) => {")
    stop_block = source[stop_start : source.index("\n};", stop_start)]
    assert "recoveryCardStoppedByUser.value = true;" in stop_block
    assert "refreshRecoveryCard();" in stop_block
    # 复位必须挂在 resetRecoveryCard 上：新提问与切会话/换身份都经它。
    reset_start = source.index("const resetRecoveryCard = () => {")
    reset_block = source[reset_start : source.index("\n};", reset_start)]
    assert "recoveryCardStoppedByUser.value = false;" in reset_block


def test_detail_reports_what_was_actually_replayed_not_the_cursor():
    """游标是后端 INCR 序号（受 LTRIM 截断影响），远大于真正补回的条数。"""
    source = (ROOT / "frontend/src/utils/recoveryCard.ts").read_text(encoding="utf-8")
    assert "本轮补回" in source
    # 旧文案把「事件序号」当「同步条数」展示，是「怎么加载这么多」的原始误导来源。
    assert "已同步 ${appliedSeq} 条事件" not in source
    assert "appliedSeq" not in source


def test_embed_chat_counts_replayed_events_instead_of_cursor_position():
    source = _embed_source()
    assert "recoverySyncedEventCount += replayEvents.length" in source
    assert "syncedEventCount: recoverySyncedEventCount" in source
    # 游标只用于 after_seq 增量定位，不能当展示用的「补回条数」。
    assert "appliedSeq: Number(activeStreamDraft?.lastAppliedSeq ?? 0) || 0" not in source


def test_embed_chat_resets_the_replayed_count_with_the_replay_state():
    source = _embed_source()
    # 两处复位：统一入口（换身份 / 丢弃当前会话 / 切会话）与新一轮提问前。
    # 按缩进计数，把文件顶部的 `let recoverySyncedEventCount = 0;` 声明排除在外。
    assert source.count("\n  recoverySyncedEventCount = 0;\n") == 2
    # 入口里必须与 streamReplayPrimed 同处复位（登记式检查见 registry 契约）。
    entry_start = source.index("const resetConversationScopedState = () => {")
    entry = source[entry_start : source.index("\n};", entry_start)]
    assert "recoverySyncedEventCount = 0;" in entry
    assert "streamReplayPrimed = false;" in entry
