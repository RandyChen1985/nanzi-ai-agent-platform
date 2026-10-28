"""契约：事件续显与客户端实时流「同一时刻只允许一个写入方」。

背景（用户反馈）：刷新 / 切页面回来后的恢复流程会把同一条 AI 回复输出两遍。
根因是恢复期间事件日志续显（每秒轮询 /stream-events 并把事件追加进草稿）与客户端
实时流会同时写同一条助手消息。SSE 帧不带事件序号，前端无从判断日志里的事件「是否
已经渲染过」，于是同一段正文被写两次——正文短片段达不到 32 字符去重阈值，思考内容
则完全没有去重（`reasoning_content` 是直接字符串拼接）。

本契约锁五件事：
1. 实时流在途时，恢复器不启动续显、不补占位（写入方唯一）；
2. 权限确认 / 外部执行恢复这类「事件日志不记录」的流占住写入方，并给草稿打上
   `resumeOwned`（随快照持久化），让续显彻底不再碰它；
3. 让位只停轮询 + 按代次丢弃在途结果，**不动**游标与草稿进度（归零会造成内容回退）；
4. 首次续显一律按日志重建（含 gap），不再「保留前缀 + 追加存活窗口」；
5. 锁态复位用「写入方令牌」判定归属，且恢复流**不提前收尾**（收尾交给落库终态同步）。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EMBED_CHAT = ROOT / "frontend/src/views/EmbedChat.vue"
pytestmark = pytest.mark.no_infrastructure

RESUME_SIGNATURES = (
    "const submitPendingExternalExecution = async (msg: Message) => {",
    # 确认函数的形参已展开成多行并新增 scope（「本会话总是允许」），只按函数头定位。
    "const confirmPendingPermission = async (",
)


def _source() -> str:
    return EMBED_CHAT.read_text(encoding="utf-8")


def _top_level_body(signature: str) -> str:
    """取一个顶层 `const xxx = ...` 的函数体（到顶格的 `};` 为止）。"""
    source = _source()
    start = source.index(signature)
    end = source.index("\n};", start)
    return source[start:end]


def _recovery_loop_body() -> str:
    """取 `scheduleRunRecoverySync` 里「闸门 + 启动续显」的那段（onMounted 内）。"""
    source = _source()
    start = source.index("const scheduleRunRecoverySync = () => {")
    end = source.index("runRecoveryTimer = setTimeout(async () => {", start)
    return source[start:end]


def _replay_schedule_body() -> str:
    source = _source()
    start = source.index("const scheduleStreamReplay = () => {")
    return source[start : source.index("const {", start)]


def _poll_body() -> str:
    source = _source()
    start = source.index("const pollConversationStreamEvents = async () => {")
    return source[start : source.index("/** 只在页面可见时轮询", start)]


def test_recovery_loop_never_starts_replay_while_a_client_stream_is_in_flight():
    """流在途时恢复器必须让位：不拉续显、不补草稿。

    闸门只能看「流是否在途」，不能拿 Stall（2s 无帧）当开闸条件：流只是静默时，
    「已渲染到哪」无从得知（SSE 帧不带事件序号），此时启动续显只能二选一——重复
    正文，或清掉已渲染内容。
    """
    body = _recovery_loop_body()
    assert "if (streamInFlight) {" in body
    assert "if (activeStreamDraft) scheduleStreamReplay();" in body
    # 续显启动必须排在「流在途」闸门之后。
    assert body.index("if (streamInFlight) {") < body.index(
        "if (activeStreamDraft) scheduleStreamReplay();"
    )
    assert (
        "runRecoveryTimer = setTimeout(scheduleRunRecoverySync, RUN_RECOVERY_SYNC_INTERVAL_MS);"
        in body
    ), "流在途时恢复器要保留自己的轮询"
    assert "!showStalledPrompt.value" not in body, "Stall 提示不得再作为续显开闸条件"


def test_schedule_stream_replay_defers_to_a_live_stream_before_touching_timers():
    """即使调用方绕过闸门，续显自身也必须让位。

    闸门必须先于 `clearStreamReplayTimer()`：先清定时器会让让位逻辑误判「没有续显
    活动」，从而不增加代次，在途轮询的结果仍会写进草稿。
    """
    body = _replay_schedule_body()
    assert "if (streamInFlight) {" in body
    assert "stopReplayForLiveStream();" in body
    assert body.index("if (streamInFlight) {") < body.index("clearStreamReplayTimer();")
    # 恢复流写出的草稿：续显彻底不碰。
    assert "if (activeStreamDraft?.resumeOwned) return;" in body


def test_stopping_replay_for_live_stream_does_not_rewind_the_cursor():
    """让位只停轮询 + 作废在途结果，不动游标 / 草稿进度 / 快照。

    归零游标会让下次首拉清掉实时流（尤其是不写事件日志的权限确认 / 外部执行恢复流）
    刚写出的正文，再按日志重建——那是内容回退，是比重复更难接受的副作用。
    """
    body = _top_level_body("const stopReplayForLiveStream = () => {")
    assert "clearStreamReplayTimer();" in body
    assert "streamReplayGeneration += 1;" in body
    # 无条件自增：让位可能发生在「别人刚清掉定时器、首个轮询仍在途」的窄窗里，
    # 用 timer/primed 做条件会漏掉那批在途结果。
    assert "hadReplayActivity" not in body
    for forbidden in (
        "streamReplayPrimed = false;",
        "lastSeq: 0",
        "lastAppliedSeq = 0",
        "maybePersistStreamSnapshot(true);",
    ):
        assert forbidden not in body, f"让位不得回退续显进度：{forbidden}"


def test_snapshot_persists_and_restores_the_resume_owned_flag():
    """`resumeOwned` 必须随快照持久化并在挂载时还原。

    刷新是本次修复的核心场景：标记只存在内存里时，重启后首拉会按「只有挂起前事件」
    的日志重建，把快照里恢复流写出的正文换回旧内容。
    """
    source = _source()
    persist_start = source.index("const persistStreamSnapshot = (")
    persist_body = source[persist_start : source.index("\n};", persist_start)]
    assert "resumeOwned = false," in persist_body
    assert "resumeOwned," in persist_body

    maybe_body = _top_level_body("const maybePersistStreamSnapshot = (force = false) => {")
    assert "active.resumeOwned === true" in maybe_body

    restore_start = source.index("const restoreEmbedStreamSnapshot = async (")
    restore_body = source[restore_start : source.index("\n};", restore_start)]
    assert "resumeOwned: snapshot.resumeOwned === true" in restore_body


def test_poll_skips_a_draft_written_by_a_resume_stream():
    """恢复流写出的草稿不参与续显：日志里只有挂起前的事件。"""
    body = _poll_body()
    assert "if (activeStreamDraft?.resumeOwned) return;" in body


def test_in_flight_poll_results_are_dropped_after_stop():
    """让位前发出的轮询结果必须整批丢弃，不能再写草稿。"""
    body = _poll_body()
    assert "const replayGeneration = streamReplayGeneration;" in body
    assert "if (replayGeneration !== streamReplayGeneration) return;" in body
    assert body.index("const replayGeneration = streamReplayGeneration;") < body.index(
        "const response = await axios.get("
    )
    # 检查必须排在「取续显目标 + 应用事件」之前。
    assert body.index("if (replayGeneration !== streamReplayGeneration) return;") < body.index(
        "const replayTarget = activeStreamDraft?.msg ?? null;"
    )


def test_first_poll_rebuilds_even_when_the_journal_was_trimmed():
    """首拉一律按日志整体重建，gap 时也不例外。

    「保留前缀 + 追加存活窗口」就是同一条回复出现两段相同正文的来源：前缀与窗口的
    重叠范围无法判定（SSE 帧不带事件序号）。被截掉的头部交给落库终态补齐。
    """
    body = _poll_body()
    assert "if (isFirstPoll && replayEvents.length > 0) {" in body
    assert "resetDraftForReplay(replayTarget);" in body
    assert "!plan.gap" not in body, "gap 不得再决定「是否重建」"


def test_resume_streams_own_the_writer_slot_and_guard_the_lock_reset():
    """权限确认 / 外部执行恢复：独占写入方、标记草稿、锁态复位带会话归属。

    刻意**不**在恢复流的 finally 里 `finishRecoveredTurn()`：恢复端点没有后台 producer，
    客户端读流中断时后端可能既不落库也不写审计，提前清快照会让这一轮刷新后永久消失。
    收尾统一交给落库终态同步（`syncLatestSessionHistory` → `planHistorySync`）。
    """
    source = _source()
    for signature in RESUME_SIGNATURES:
        start = source.index(signature)
        body = source[start : source.index("\n};", start)]
        assert "stopReplayForLiveStream();" in body, signature
        assert "streamInFlight = true;" in body, signature
        assert "streamInFlight = false;" in body, signature
        assert "activeStreamDraft.resumeOwned = true;" in body, signature
        # 先让续显让位，再把自己标记为「流在途」。
        assert body.index("stopReplayForLiveStream();") < body.index(
            "streamInFlight = true;"
        ), signature
        # 锁态复位必须按「写入方令牌」判归属：切会话 / 新的流接管后，旧流收尾不得再动
        # 新会话的单写入方标志（否则 SSE 与续显再次同写）；同时在本会话另开新会话时，
        # 因为没有新写入方，令牌仍属于自己，锁态必须被复位（否则永久锁死）。
        assert "const resumeToken = ++liveWriterToken;" in body, signature
        assert "if (liveWriterToken === resumeToken) {" in body, signature
        assert body.index("const resumeToken = ++liveWriterToken;") < body.index(
            "if (liveWriterToken === resumeToken) {"
        ) < body.index("streamInFlight = false;"), signature
        # 不在恢复流里提前收尾（快照/草稿由落库终态同步清理）。
        assert "finishRecoveredTurn();" not in body, signature


def test_resume_streams_rebind_the_draft_and_persist_snapshots():
    """恢复流必须把草稿重新绑定到被确认的消息，并按节流落盘。

    主流收尾（`sendMessageInternal` 收到 [DONE]）已经把 `activeStreamDraft` 置空，
    而恢复流要等用户点确认才启动。不重新绑定的话：

    - `maybePersistStreamSnapshot()` 因草稿为空直接 return —— 恢复流期间刷新 / 切后台
      时已输出的正文一个字都存不进快照，刷新后只能看到挂起前内容（内容回退）；
    - `resumeOwned` 也无处可打，即使有快照也会被首拉按日志重建掉。

    同时恢复流没有后台 producer，必须按 1.5s 节流把增量写进快照。
    """
    source = _source()
    for signature in RESUME_SIGNATURES:
        start = source.index(signature)
        body = source[start : source.index("\n};", start)]
        # 已绑定到同一条消息就只补标记；否则重新绑定（主流结束时草稿已为 null）。
        assert "if (activeStreamDraft?.msg === msg) {" in body, signature
        assert "conversationId: conversationId.value," in body, signature
        assert "lastAppliedSeq: Number(streamReplayCursor.value.lastSeq || 0) || 0," in body, signature
        assert "resumeOwned: true," in body, signature
        # 绑定必须早于「流在途」标记，保证后续 pagehide / 事件落盘都有草稿可用。
        assert body.index("resumeOwned: true,") < body.index("streamInFlight = true;"), signature
        # 恢复流按节流把增量写进快照（外部流在 onEvent、确认流在两个读流循环里）。
        assert "maybePersistStreamSnapshot();" in body, signature


def test_recovery_draft_is_not_fabricated_under_a_live_stream_or_after_a_local_answer():
    """不补占位：流在途时正文有确定写入方；本轮已渲染出完整答案时也不能再补一条。

    判「有完整答案」前必须剥掉本端传输错误后缀：只有一句「[错误: …]」不代表真实产出，
    否则该轮既不补草稿、也拿不到续显，会永久停在错误后缀上。
    """
    body = _top_level_body("const ensureRecoveryDraft = async () => {")
    assert "if (streamInFlight) return false;" in body
    assert 'lastMeaningful?.role === "agent"' in body
    assert "lastMeaningful.isHistory !== true" in body
    assert "stripClientStreamErrorSuffix(" in body
    assert "lastMeaningful.clientTransportErrorSuffix" in body


def test_new_turn_resets_and_invalidates_replay_progress():
    """开新一轮时必须断掉上一轮排着的续显，并作废其代次。"""
    source = _source()
    start = source.index("const sendMessageInternal = async")
    body = source[start : source.index("const streamConversationId", start)]
    assert "clearStreamReplayTimer();" in body
    assert "streamReplayGeneration += 1;" in body


def test_every_progress_reset_path_bumps_the_generation():
    """代次自增至少覆盖：让位、开新一轮、切会话复位、本轮收敛。"""
    source = _source()
    assert source.count("streamReplayGeneration += 1;") >= 4
    finish_body = _top_level_body("const finishRecoveredTurn = () => {")
    assert "streamReplayGeneration += 1;" in finish_body


def test_new_writer_takes_over_the_lock_ownership():
    """新一轮开流与切会话复位都要换掉写入方令牌。

    - 新一轮开流：旧恢复流随后收尾时令牌已不是它 → 不动本轮锁态（避免清掉单写入方标志）；
    - 切会话复位：旧恢复流收尾时同样让位（此时锁态已由复位入口归零）。
    """
    source = _source()
    start = source.index("const sendMessageInternal = async")
    send_body = source[start : source.index("const streamConversationId", start)]
    assert "liveWriterToken += 1;" in send_body
    assert send_body.index("streamInFlight = true;") < send_body.index("liveWriterToken += 1;")

    reset_body = _top_level_body("const resetConversationScopedState = () => {")
    assert "liveWriterToken += 1;" in reset_body


def test_restored_resume_snapshot_revives_the_interrupted_card():
    """恢复流写出的快照 + 该轮未落库 → 卡片复活为 pending，刷新后可直接重试。

    恢复流第一帧就会把卡片改成「已批准 / 已完成」；若这一轮没落库，说明恢复轮被
    刷新 / 断网取消了（后端挂起快照仍在）。不复活的话用户只剩一张已批准的卡片，
    没有重试入口——正文保住了，这一轮却接不上。用户主动拒绝不复活。
    """
    source = _source()
    start = source.index("const restoreEmbedStreamSnapshot = async (")
    restore_body = source[start : source.index("\n};", start)]
    assert "snapshot.resumeOwned === true && traceId" in restore_body
    assert "reviveInterruptedResumeCards(restoredAgent);" in restore_body

    helper = _top_level_body("const reviveInterruptedResumeCards = (msg: Message) => {")
    for expected in (
        'permission.status === "approved"',
        'permission.status === "error"',
        'permission.status = "pending";',
        'external.status === "completed"',
        'external.status === "error"',
        'external.status = "pending";',
        "isSubmitting = false;",
    ):
        assert expected in helper, expected
    assert '"rejected"' not in helper, "用户主动拒绝不是被中断，不能复活"
