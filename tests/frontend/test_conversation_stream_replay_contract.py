"""契约：EmbedChat 事件续显的进度合并纯函数。"""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

MODULE_PATH = "frontend/src/utils/conversationStreamReplay.ts"


def _call(expression: str):
    script = f"""
(async () => {{
const fs = require('fs');
const ts = require('./frontend/node_modules/typescript');
const source = fs.readFileSync({json.dumps(MODULE_PATH)}, 'utf8');
const code = ts.transpileModule(source, {{
  compilerOptions: {{ module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 }}
}}).outputText;
const moduleRef = {{ exports: {{}} }};
new Function('module', 'exports', 'require', code)(moduleRef, moduleRef.exports, require);
const api = moduleRef.exports;
const result = await (async () => {{ {expression} }})();
process.stdout.write(JSON.stringify(result === undefined ? null : result));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    completed = subprocess.run(
        ["node", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return json.loads(completed.stdout)


def test_keeps_events_after_cursor_and_advances():
    result = _call(
        """
return api.planStreamReplay(
  [
    { _seq: 1, type: 'log', title: 'a' },
    { _seq: 2, type: 'log', title: 'b' },
    { _seq: 3, type: 'log', title: 'c' },
  ],
  { lastSeq: 1, traceId: 't1' },
)
"""
    )
    assert [event["title"] for event in result["events"]] == ["b", "c"]
    assert result["lastSeq"] == 3
    assert result["gap"] is False


def test_detects_gap_when_journal_trimmed():
    result = _call(
        """
return api.planStreamReplay(
  [ { _seq: 9, type: 'log', title: 'x' } ],
  { lastSeq: 2, traceId: 't1' },
)
"""
    )
    assert result["gap"] is True
    assert result["lastSeq"] == 9


def test_drops_events_from_another_turn():
    result = _call(
        """
return api.planStreamReplay(
  [
    { _seq: 1, type: 'log', title: 'mine', trace_id: 't1' },
    { _seq: 2, type: 'log', title: 'stale', trace_id: 't0' },
    { _seq: 3, type: 'log', title: 'no-trace' },
  ],
  { lastSeq: 0, traceId: 't1' },
)
"""
    )
    assert [event["title"] for event in result["events"]] == ["mine", "no-trace"]


def test_ignores_malformed_events_and_keeps_cursor():
    result = _call(
        """
return api.planStreamReplay(
  [ null, 'nope', { type: 'log', title: 'no-seq' }, { _seq: 4, type: 'log', title: 'ok' } ],
  { lastSeq: 3, traceId: '' },
)
"""
    )
    assert [event["title"] for event in result["events"]] == ["ok"]
    assert result["lastSeq"] == 4


def test_empty_batch_echoes_cursor():
    result = _call(
        """
return api.planStreamReplay([], { lastSeq: 12, traceId: 't1' })
"""
    )
    assert result["events"] == []
    assert result["lastSeq"] == 12
    assert result["gap"] is False


EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
RECOVERY_CARD_MODULE = ROOT / "frontend/src/utils/recoveryCard.ts"


def _embed_source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _recovery_card_source() -> str:
    return RECOVERY_CARD_MODULE.read_text(encoding="utf-8")


def test_embed_chat_polls_stream_events_with_cursor():
    source = _embed_source()
    assert 'from "@/utils/conversationStreamReplay"' in source
    assert "const pollConversationStreamEvents = async (" in source
    assert "STREAM_REPLAY_POLL_INTERVAL_MS" in source
    assert "/conversation/${encodeURIComponent(cid)}/stream-events" in source
    # 起点取「游标」与「草稿已应用进度」的较大者：游标可能落后于草稿真实进度，
    # 只信它会把同一批事件再叠加一遍。
    assert "params: { after_seq: afterSeq }," in source


def test_embed_chat_applies_replay_events_through_shared_dispatcher():
    source = _embed_source()
    # 必须复用既有分发器，避免恢复期与正常流的渲染逻辑分叉
    assert "applyPermissionStreamEvent(replayTarget, event, { replay: true });" in source
    assert "planStreamReplay(" in source


def test_embed_chat_persists_replay_cursor_into_snapshot():
    source = _embed_source()
    # 落盘进度取游标与草稿已应用进度的较大者，避免切回后重放已渲染过的事件。
    assert "Number(streamReplayCursor.value.lastSeq || 0) || 0," in source
    assert "Number(active.lastAppliedSeq || 0) || 0," in source


def test_embed_chat_rebuilds_draft_from_full_log_on_first_poll():
    """首次拉取必须「清空累加字段 + 全量重放」，而不是只对齐游标。

    只对齐会让「切走期间后台新产出的思考/正文/工具节点」永久缺失；直接重放又会
    让快照前缀叠加两遍。断层（日志被截断）时同样要重建：前缀与存活窗口的重叠范围
    无法判定（SSE 帧不带序号），「保留前缀 + 追加窗口」会把重叠段再写一遍；被截掉的
    头部交给落库终态补齐。
    """
    source = _embed_source()
    # 必须断言「调用」而不是「符号出现」：import 语句同样包含这些名字
    assert "shouldResetDraftBeforeReplay(" in source
    assert "resetDraftForReplay(replayTarget);" in source, "首次拉取必须真的调用重建"
    assert "let streamReplayPrimed" in source
    assert "if (isFirstPoll && replayEvents.length > 0) {" in source
    assert "!plan.gap" not in source, "gap 不得再决定「是否重建」"
    # 终态重置，保证下一轮任务仍会重新判定
    assert "streamReplayPrimed = false" in source


def test_embed_chat_drops_stream_draft_after_normal_completion():
    """流正常读完后必须清掉在途草稿引用。

    否则在「SSE 已结束、run-status 尚未翻回 false」的时间窗里，运行恢复器会把
    残留草稿误判为断线在途：既误弹「会话仍在处理中」提示，又白拉一次事件日志。
    清理必须落在正常完成路径（try 末尾），异常/中断路径要保留草稿供恢复。
    """
    source = _embed_source()
    # 必须从 sendMessageInternal 内部定位：整个文件里另有 4 处同名 `} catch (e: any) {`
    function_start = source.index("const sendMessageInternal = ")
    catch_anchor = source.index("  } catch (e: any) {", function_start)
    before_catch = source[:catch_anchor]
    completion_tail = before_catch[before_catch.rindex("flushContentBuffer();") :]
    assert "activeStreamDraft = null;" in completion_tail, (
        "正常完成路径应清掉在途草稿，避免被运行恢复器误判为断线在途"
    )
    assert "clearStreamReplayTimer();" in completion_tail


def test_embed_chat_resets_replay_trace_on_new_turn():
    """新一轮提问时必须清掉上一轮残留的 traceId（lastSeq 必须保留）。

    seq 跨轮次全局单调，lastSeq 是本轮事件的正确起点；但 traceId 若残留上一轮，
    planStreamReplay 会把本轮事件整批当成「上一轮残留」而跳过，首批增量永久丢失。
    触发路径很常见：切回页面（快照恢复出 traceId）→ 本轮结束 → 同一实例里继续提问。
    """
    source = _embed_source()
    marker = 'streamReplayCursor.value = { lastSeq: 0, traceId: "" };'
    assert marker in source, (
        "新一轮启动必须把续显游标整体归零：后端每轮会清空日志并让 _seq 从 1 重新计数，"
        "保留 lastSeq 会让本轮事件全部落在 after_seq 内被丢弃，保留 traceId 会放行上一轮残留"
    )
    reset = source.index(marker)
    draft = source.index("activeStreamDraft = { conversationId: conversationId.value")
    assert reset < draft, "游标清理应发生在新一轮登记草稿之前"
    assert "streamReplayPrimed = false;" in source[reset:draft], "新一轮必须复位首次对齐标志"


def test_reset_draft_for_replay_clears_accumulated_fields_only():
    """重放前只清「累加型展示字段」，交互态与身份必须原样保留。

    content/reasoning/时间线/日志是逐条累加的，不清就会在重放时叠加成两遍；
    而权限请求、用户追问等交互态是 upsert 终态，清空后重放可能把用户已经处理过的
    请求回退成待处理。
    """
    result = _call(
        "const draft = {"
        "  id: 'm1', role: 'agent', trace_id: 't1', content: '前半段',"
        "  reasoningContent: '思考中', processTimeline: [{ id: 1 }], logs: [{ id: 1 }],"
        "  processNarration: '旁白', processNarrationPending: '待提交', citations: [{}],"
        "  isThinking: true, isThoughtExpanded: true,"
        "  pendingPermission: { status: 'pending' }, userQuestion: { id: 'q1' }"
        "};"
        "api.resetDraftForReplay(draft);"
        "return draft;"
    )
    # 累加型展示字段：必须清空，交由日志整体重建
    assert result["content"] == ""
    assert result.get("reasoningContent") is None
    assert result["processTimeline"] == []
    assert result["logs"] == []
    assert result["processNarration"] == ""
    assert result["processNarrationPending"] == ""
    assert result["citations"] == []
    assert result["isThinking"] is False
    # 交互态、身份、UI 偏好：不得被回退
    assert result["pendingPermission"] == {"status": "pending"}
    assert result["userQuestion"] == {"id": "q1"}
    assert result["trace_id"] == "t1"
    assert result["id"] == "m1"
    assert result["isThoughtExpanded"] is True


def test_embed_chat_does_not_start_replay_polling_while_stream_is_healthy():
    """正常流推进中不得启动续显轮询（同一时刻只允许一个写入方）。

    SSE 此时已在实时推送同一批事件，再轮询不仅会给普通对话叠加每秒一次的
    stream-events 请求，还会把已渲染过的事件再写一遍——正文短片段达不到 32 字符
    去重阈值、思考内容完全没有去重，表现为同一条回复出现两段相同正文。
    闸门只看「流是否在途」：Stall（2s 无帧）不代表流已死，此时「已渲染到哪」无从
    得知（SSE 帧不带事件序号），放续显进来只能二选一（重复正文 / 清掉已渲染内容）。
    """
    source = _embed_source()
    start = source.index("const scheduleRunRecoverySync = () => {")
    body = source[start : source.index("runRecoveryTimer = setTimeout(async () => {", start)]
    assert "if (streamInFlight) {" in body
    assert body.index("if (streamInFlight) {") < body.index(
        "if (activeStreamDraft) scheduleStreamReplay();"
    ), "scheduleStreamReplay 必须在「流在途」闸门之后调用"
    assert "!showStalledPrompt.value" not in body, "Stall 提示不得再作为续显开闸条件"

    # 续显入口自身再兜一层：调用方绕过闸门时也必须让位（只停轮询，不回退游标）。
    replay_start = source.index("const scheduleStreamReplay = () => {")
    replay_body = source[replay_start : source.index("const {", replay_start)]
    assert "if (streamInFlight) {" in replay_body
    assert "stopReplayForLiveStream();" in replay_body


def test_plan_should_reset_draft_before_replay_on_first_poll():
    """切回/刷新后首次续显，必须先把草稿的累加字段清空再整体重放。

    草稿只是「切走瞬间」的前缀，而日志同时含切走期间后台新产出的事件：直接重放会
    把前缀叠加一遍（短片段去重阈值 32 字符，逐条 delta 无法识别为重复），只对齐
    游标又会让切走期间的内容永久缺失。清空后整体重建才同时满足这两点。
    """
    cases = [
        ({"lastSeq": 0}, True, False, True, "草稿已渲染且游标未知 → 只对齐、不重放"),
        ({"lastSeq": 0}, True, True, False, "已对齐过 → 走正常增量"),
        ({"lastSeq": 42}, True, False, False, "游标已知 → 走正常增量"),
        ({"lastSeq": 0}, False, False, False, "草稿为空 → 必须全量重放"),
    ]
    for cursor, has_content, primed, expected, label in cases:
        result = _call(
            f"return api.shouldResetDraftBeforeReplay({json.dumps(cursor)}, "
            f"{{ hasContent: {str(has_content).lower()} }}, {str(primed).lower()})"
        )
        assert result is expected, label


def test_embed_chat_notifies_when_replay_channel_takes_over():
    """续显通道接管实时推进时，应让用户看到「会话恢复」提示卡片。

    否则用户切回来只看到界面在动，无法判断任务到底还在不在跑。提示挂在续显
    启动处，因此正常对话（SSE 健康、续显根本不启动）不会误弹。提示形态已从 toast
    升级为可关闭的状态卡片（见 test_recovery_card_contract.py），文案交给
    planRecoveryCard 的纯函数决策。
    """
    source = _embed_source()
    start = source.index("const scheduleStreamReplay = () => {")
    body = source[start : start + 900]
    assert "refreshRecoveryCard();" in body, "续显启动处应刷新恢复提示卡片"
    assert "showToast(" not in body, "恢复提示不应再走 toast"
    assert "已恢复实时同步" in _recovery_card_source(), "提示文案应说明已恢复实时同步"


def test_draft_binding_requires_the_very_same_conversation():
    """草稿归属必须严格相等：空归属、别人的会话都不算绑定。"""
    assert _call("return api.isDraftBoundToConversation({ conversationId: 'c1' }, 'c1')") is True
    assert _call("return api.isDraftBoundToConversation({ conversationId: 'c1' }, 'c2')") is False
    assert _call("return api.isDraftBoundToConversation({ conversationId: '' }, '')") is False
    assert _call("return api.isDraftBoundToConversation(null, 'c1')") is False
    assert _call("return api.isDraftBoundToConversation({}, 'c1')") is False


def test_conversation_switch_rebinds_unless_the_draft_already_belongs_to_the_target():
    """切会话要复位续显：只有草稿已属于目标会话时才无需重绑。"""
    assert _call("return api.shouldRebindReplayOnConversationSwitch('c1', 'c2')") is True
    assert _call("return api.shouldRebindReplayOnConversationSwitch(null, 'c2')") is True
    assert _call("return api.shouldRebindReplayOnConversationSwitch(undefined, 'c2')") is True
    assert _call("return api.shouldRebindReplayOnConversationSwitch('c1', 'c1')") is False
    # 目标为空（无效切换）不动任何状态。
    assert _call("return api.shouldRebindReplayOnConversationSwitch('c1', '')") is False
