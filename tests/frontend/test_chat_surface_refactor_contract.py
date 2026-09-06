from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_both_chat_surfaces_use_shared_saved_report_workflow():
    shared = _read("frontend/src/composables/chat/useSavedReportWorkflow.ts")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for name in (
        "detectSavedReportDateTemplate",
        "todayDateString",
        "todayMonthString",
        "parseSavedReportTags",
        "renderSavedReportDataToMarkdown",
        "composeSavedReportExecuteMarkdown",
        "extractColumnMetaFromAgentMessage",
        "mergeSavedReportAnalysisIntoResult",
        "buildSavedReportRunParams",
        "extractSavedReportExecuteErrorMessage",
    ):
        assert f"export const {name}" in shared
        if name == "renderSavedReportDataToMarkdown":
            # 表格渲染由 compose 内部复用，页面入口改为 composeSavedReportExecuteMarkdown
            continue
        assert name in embed
        assert name in debug
        assert f"const {name} =" not in embed
        assert f"const {name} =" not in debug

    assert '@/composables/chat/useSavedReportWorkflow' in embed
    assert '@/composables/chat/useSavedReportWorkflow' in debug


def test_chat_surface_refactor_keeps_existing_shared_feature_entrypoints():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for source in (embed, debug):
        for contract in (
            "useDatasetPortal",
            "useKnowledgePortal",
            "handleExecuteSavedReport",
            "handleWorkspaceFilePreview",
            "stopGeneration",
            "confirmPendingPermission",
            "submitPendingExternalExecution",
            "ChatCanvas",
            "DatasetPortalDrawer",
            "KnowledgePortalDrawer",
        ):
            assert contract in source


def test_chat_surfaces_keep_permission_and_external_execution_panels():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    assert 'v-if="msg.pendingPermission"' not in timeline
    assert "confirmPendingPermission" not in timeline
    for source in (embed, debug):
        timeline_at = source.find("<ChatExecutionTimeline")
        permission_at = source.find('v-if="msg.pendingPermission"')
        external_at = source.find('v-if="msg.pendingExternalExecution"')
        assert timeline_at >= 0
        assert permission_at > timeline_at
        assert external_at > permission_at
        assert '@click="confirmPendingPermission(msg, true)"' in source
        assert '@click="confirmPendingPermission(msg, false)"' in source
        assert '@click="submitPendingExternalExecution(msg)"' in source
        assert "允许" in source[permission_at:external_at]
        assert "拒绝" in source[permission_at:external_at]


def test_external_execution_resume_consumes_shared_sse_parser_payloads_directly():
    shared = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    resume_stream = shared[shared.index("export async function resumeExternalExecutionStream") :]

    assert "for (const payload of lines)" in resume_stream
    assert "for (const payload of parser.flush())" in resume_stream
    assert 'startsWith("data:")' not in resume_stream


def test_stop_generation_cancels_backend_run_before_aborting_sse():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")
    util = _read("frontend/src/utils/cancelConversationRun.ts")

    assert "/api/v1/chat/cancel" in util
    for source in (embed, debug):
        cancel_at = source.find("cancelConversationRun(")
        abort_at = source.find("abortController.abort()")
        stop_at = source.find("const stopGeneration")
        assert cancel_at > stop_at > 0
        assert abort_at > cancel_at



def test_both_chat_surfaces_use_shared_workspace_canvas_lifecycle():
    shared = _read("frontend/src/composables/chat/useWorkspaceCanvas.ts")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for name in (
        "canvasVisible",
        "canvasFromWorkspace",
        "canvasData",
        "handleWorkspaceFilePreview",
        "handleOpenCanvas",
        "closeCanvas",
        "revokeActiveBlobUrl",
    ):
        assert name in shared
        assert name in embed
        assert name in debug

    for source in (embed, debug):
        assert "useWorkspaceCanvas" in source
        assert "const canvasVisible = ref" not in source
        assert "const handleWorkspaceFilePreview =" not in source
        assert "const handleOpenCanvas =" not in source

    assert "URL.revokeObjectURL" in shared
    assert "openWorkspaceFileInCanvas" in shared
    assert "shouldAttachWorkspaceSourcePath" in shared


def test_both_chat_surfaces_use_shared_attachment_context_builder():
    shared = _read("frontend/src/composables/chat/useChatAttachments.ts")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for source in (embed, debug):
        assert "useChatAttachments" in source
        assert "const buildImageAttachmentHint =" not in source
        assert "const buildSkillAttachmentHint =" not in source
        assert "const appendAttachmentContext =" not in source

    assert "export const useChatAttachments" in shared
    assert "buildKnowledgeBaseAttachmentHint" in shared
    assert "USER_MESSAGE_CONTEXT_DIVIDER" in shared
    assert "getServerAttachmentPath" in shared
    assert "isImageAttachment" in shared


def test_both_chat_surfaces_use_shared_history_date_grouping():
    shared = _read("frontend/src/composables/chat/useChatHistoryGroups.ts")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert "export const groupChatHistoryByDate" in shared
    assert 'title: "今天"' in shared
    assert 'title: "更早"' in shared
    for source in (embed, debug):
        assert "groupChatHistoryByDate" in source
        assert "const groupedHistoryList = computed(() =>" in source
        assert 'today: { title: "今天"' not in source


def test_both_chat_surfaces_use_shared_thinking_header_component():
    component = _read("frontend/src/components/chat/ChatThinkingHeader.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    assert 'defineModel<boolean>("expanded"' in component
    assert "isThinking" in component
    assert "stepCount" in component
    assert "hiddenStepCount" in component
    assert "skillSummary" in component
    assert "<ChatThinkingHeader" in timeline
    assert 'v-model:expanded="expanded"' in timeline
    for source in (embed, debug):
        assert "<ChatThinkingHeader" not in source
        assert "<ChatExecutionTimeline" in source
        assert 'v-model="msg.isThoughtExpanded"' in source
        assert "appendAssistantBodyDelta" in source
        assert ':skill-badges="getSkillFlowBadgesForMessage(msg, messages)"' in source
    assert ":title=\"headerTitle\"" in timeline
    assert '"执行完成"' in timeline
    assert "resolveTimelineCurrentStep" in timeline
    assert "timelineHasPending" in timeline
    assert ':is-thinking="!hasAnswer && (isThinking || hasPending)"' in timeline
    assert 'if (pending && !props.hasAnswer)' in timeline
    assert 'if (answer) expanded.value = false' in timeline
    assert "过程消息" not in timeline
    assert "reasoning" in timeline
    assert "tool" in timeline
    assert "mergeTimelineLogs" in timeline
    assert "skillBadges" in timeline
    assert "skillNoticeLabel" in timeline
    assert "headerSkillSummary" in timeline
    assert "w-full max-w-[90%] min-w-0" in embed


def test_both_chat_surfaces_share_stream_trace_and_citation_normalization():
    shared = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for name in ("applyStreamTraceId", "mergeStreamCitations"):
        assert f"export function {name}" in shared
        assert name in embed
        assert name in debug
    assert "chunk_id" in shared
    assert "doc_name" in shared
    assert "data.data?.trace_id || data.trace_id" in shared
    assert 'msg.trace_id = traceId as T["trace_id"]' in shared


def test_embed_chat_uses_shared_sse_parser_for_process_events():
    embed = _read("frontend/src/views/EmbedChat.vue")

    assert "const sseLineParser = createSseLineParser();" in embed
    assert "sseLineParser.feed(decoder.decode(value, { stream: true }))" in embed
    assert 'let buffer = ""; // 缓冲区，用于处理跨 chunk 的不完整行' not in embed


def test_embed_chat_keeps_ltm_state_outside_workspace_canvas_extraction():
    embed = _read("frontend/src/views/EmbedChat.vue")
    send_message_index = embed.index("const sendMessage = async () =>")

    for declaration in (
        "const activeLtmPreference = ref<any>(null)",
        "const ignoreLtmThisTurn = ref(false)",
        "const ltmAlertedInSession = ref(false)",
        "const handleIgnoreLtm = () =>",
    ):
        assert embed.count(declaration) == 1
        assert embed.index(declaration) < send_message_index

    assert "watch(conversationId, () =>" in embed
    assert "const ltmIgnoredVal = ignoreLtmThisTurn.value" in embed
    assert ':active-ltm-preference="activeLtmPreference"' in embed
    assert '@ignore-ltm="handleIgnoreLtm"' in embed


def test_agent_message_actions_wait_until_stream_finishes():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert 'v-if="!(isProcessing && msg.id === lastAgentMessage?.id)"' in embed
    assert "flex min-w-0 max-w-full flex-nowrap items-center space-x-2 overflow-x-auto mt-1 scrollbar-hide" in embed
    assert embed.index('v-if="!(isProcessing && msg.id === lastAgentMessage?.id)"') < embed.index(
        "flex min-w-0 max-w-full flex-nowrap items-center space-x-2 overflow-x-auto mt-1 scrollbar-hide"
    )
    assert "!(isProcessing && messages.indexOf(msg) === messages.length - 1)" in debug
    assert "title=\"复制\"" in embed
    assert "title=\"复制\"" in debug
