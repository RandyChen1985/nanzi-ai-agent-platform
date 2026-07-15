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
        "buildSavedReportRunParams",
        "extractSavedReportExecuteErrorMessage",
    ):
        assert f"export const {name}" in shared
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

    assert 'defineModel<boolean>("expanded"' in component
    assert "isThinking" in component
    assert "stepCount" in component
    assert "hiddenStepCount" in component
    assert "skillSummary" in component
    for source in (embed, debug):
        assert "<ChatThinkingHeader" in source
        assert "v-model:expanded=\"msg.isThoughtExpanded\"" in source


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
