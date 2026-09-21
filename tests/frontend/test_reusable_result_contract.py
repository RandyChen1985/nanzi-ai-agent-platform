from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "frontend/src/api/artifact.ts"
DRAWER = ROOT / "frontend/src/components/embed/MyArtifactsDrawer.vue"
LIST = ROOT / "frontend/src/components/embed/ReusableResultList.vue"
STATUS = ROOT / "frontend/src/components/chat/ReusableResultStatus.vue"
NOTICE = ROOT / "frontend/src/components/chat/ReusableResultNotice.vue"
EMBED = ROOT / "frontend/src/views/EmbedChat.vue"
ACTION_MENU = ROOT / "frontend/src/components/chat/MessageActionMenus.vue"


def test_reusable_result_api_exposes_conversation_scoped_safe_list():
    source = API.read_text(encoding="utf-8")

    assert "conversation_id?: string" in source
    assert "ReusableResultListItem" in source
    assert "reusableResults" in source
    assert "/api/v1/chat/reusable-results" in source
    assert "conversation_id" in source
    assert "result_id" in source
    assert "trace_id?: string | null" in source


def test_artifacts_drawer_has_files_and_reusable_result_tabs():
    source = DRAWER.read_text(encoding="utf-8")

    assert "文件产物" in source
    assert "可复用结果" in source
    assert "ReusableResultList" in source
    assert "select-reusable-result" in source
    assert "conversationId" in source
    assert "traceId?: string | null" in source
    assert ':trace-id="props.traceId"' in source
    assert "outputScope" in source
    assert "conversation" in source
    assert "message" in source
    assert "conversation_id: props.conversationId" in source
    assert "trace_id: outputScope.value === 'message'" in source
    assert "本会话全部" in source
    assert "本次消息" in source
    assert ':scope="outputScope"' in source


def test_reusable_result_components_expose_selection_and_status_entry():
    list_source = LIST.read_text(encoding="utf-8")
    status_source = STATUS.read_text(encoding="utf-8")

    assert "defineEmits" in list_source
    assert "reusableResults" in list_source
    assert "选择用于下一轮" in list_source
    assert "过期" in list_source
    assert "defineEmits" in status_source
    assert "可复用数据" in status_source
    assert "引用上一轮数据" in status_source
    assert "已保存" not in status_source
    assert "已复用" not in status_source
    assert "status === 'saved' || status === 'reused'" in status_source
    assert "@click=\"emit('open')\"" in status_source


def test_reusable_result_status_uses_user_facing_copy_with_parenthesized_session_count():
    status_source = STATUS.read_text(encoding="utf-8")
    embed_source = EMBED.read_text(encoding="utf-8")

    assert "count?: number | null" in status_source
    assert "可复用数据" in status_source
    assert "· {{ count }}" not in status_source
    assert "({{ count }})" in status_source
    assert "· 查看可复用结果" not in status_source
    assert "reusableResultCountByTrace" in embed_source
    assert "traceResultCount" in embed_source
    assert ":reusable-count=" in embed_source
    assert "本次复用" in LIST.read_text(encoding="utf-8")
    assert ":reused-result-id=\"props.reusedResultId\"" in DRAWER.read_text(encoding="utf-8")
    assert "reusedReusableResultId" in embed_source
    assert ':reused-result-id="reusedReusableResultId"' in embed_source


def test_message_action_menus_group_data_file_and_low_frequency_actions():
    source = ACTION_MENU.read_text(encoding="utf-8")

    for label in ("数据 / 文件", "查看可复用结果", "导出数据（Excel）", "查看文件产物", "更多", "重新生成", "查看执行链路", "查看调用详情", "添加固化报表"):
        assert label in source
    assert "本次回答引用了上一轮数据" not in source
    assert source.count("emit('regenerate')") == 1
    assert source.index('v-if="canRegenerate"') < source.index('v-if="openMenu === \'more\'"')
    # 「更多」按钮是中性无边框样式（font-medium + hover 灰底），不是描边按钮
    more_title_pos = source.index('title="更多操作"')
    more_button_class = source[source.rindex(' class="', 0, more_title_pos):more_title_pos]
    assert "font-medium" in more_button_class
    assert "hover:bg-gray-100" in more_button_class
    assert "border" not in more_button_class
    assert 'class="rounded-md border border-gray-200 px-2 py-1 text-[10px]' not in source
    assert source.index('openMenu === \'more\'') < source.index("导出数据（Excel）")
    assert "props.canExport" in source
    assert "showDataOnMobile?: boolean" in source
    assert "reusableResultId?: string | null" in source
    assert "hasConversationDataFile?: boolean" in source
    assert "sm:hidden" in source
    assert 'v-if="(mode === \'data\' || mode === \'both\')"' in source
    assert 'v-if="(mode === \'data\' || mode === \'both\') && hasDataFile"' not in source
    assert "const hasDataFile = computed(() => Boolean(props.hasConversationDataFile))" in source
    assert ':disabled="!hasDataFile"' in source
    assert "本会话暂无数据或文件" in source
    assert "@click=\"hasDataFile && toggle('data')\"" in source
    embed_source = EMBED.read_text(encoding="utf-8")
    assert "const currentMessageReusableCount = (msg: Message): number =>" in embed_source
    assert "return Math.max(traceResultCount," in embed_source
    assert "msg.hasDataOutput" in embed_source
    assert "const hasDesktopMore = computed(() => Boolean(" in source
    assert "'sm:hidden': showDataOnMobile && !hasDesktopMore" in source
    assert "props.showDataOnMobile && (hasDataFile.value || props.canExport)" in source


def test_message_action_menu_hides_desktop_export_entry_on_mobile():
    source = ACTION_MENU.read_text(encoding="utf-8")

    assert 'class="menu-item desktop-export-item"' in source
    assert "@media (min-width: 640px)" in source


def test_message_action_menu_shows_conversation_totals_inside_resource_menu():
    menu_source = ACTION_MENU.read_text(encoding="utf-8")
    embed_source = EMBED.read_text(encoding="utf-8")

    assert "conversationReusableCount?: number" in menu_source
    assert "conversationArtifactCount?: number" in menu_source
    assert "{{ conversationReusableCount }} 条" in menu_source
    assert "{{ conversationArtifactCount }} 个" in menu_source
    assert "const conversationArtifactCount = computed(() =>" in embed_source
    assert ":conversation-reusable-count=" in embed_source
    assert ":conversation-artifact-count=" in embed_source


def test_continue_analysis_buttons_use_neutral_visual_treatment():
    for path in (
        ROOT / "frontend/src/components/chat/MessageContinueAnalysis.vue",
        ROOT / "frontend/src/components/chatbi/ChatBIContinueAnalysis.vue",
    ):
        source = path.read_text(encoding="utf-8")
        assert "hover:bg-gray-100" in source
        assert "hover:bg-indigo-50" not in source


def test_chat_views_handle_server_terminal_status_before_done():
    for source_path in (EMBED, ROOT / "frontend/src/views/AgentDebug.vue"):
        source = source_path.read_text(encoding="utf-8")
        assert 'data.type === "run_status"' in source
        assert "data.status === \"success\"" in source
        assert "markOutputCompleted()" in source


def test_run_status_allows_followup_input_while_backend_finishes_persistence():
    source = (ROOT / "frontend/src/composables/chat/useConversationRunStatus.ts").read_text(encoding="utf-8")
    assert "let outputCompleted = false" in source
    assert "const markOutputCompleted = () =>" in source
    assert "nextStatus.active && !outputCompleted" in source
    assert "status.value.active" in source


def test_reusable_result_notice_explains_reused_previous_result():
    source = NOTICE.read_text(encoding="utf-8")

    assert 'role="note"' in source
    assert "引用提示" in source
    assert "本次回答基于" in source
    assert "可复用结果" in source
    assert "originName" in source


def test_embed_chat_wires_status_event_selection_and_one_shot_request_id():
    source = EMBED.read_text(encoding="utf-8")

    assert 'import MessageActionMenus from "@/components/chat/MessageActionMenus.vue"' in source
    assert 'import ReusableResultNotice from "@/components/chat/ReusableResultNotice.vue"' in source
    assert 'import ReusableResultList from "@/components/embed/ReusableResultList.vue"' not in source
    assert "reusableResultStatus" in source
    assert "selectedReusableResultId" in source
    assert "focusedReusableResultId" in source
    assert "reusable_result_id" in source
    assert "reusable_result_status" in source
    assert 'mode="data"' in source
    assert 'mode="more"' in source
    more_component = source[source.index('mode="more"'):]
    assert ':can-export="Boolean(msg.trace_id)"' in more_component
    assert ':show-data-on-mobile="true"' in more_component
    assert ':reusable-result-id="msg.reusableResultStatus?.resultId"' in more_component
    assert ':reusable-count="currentMessageReusableCount(msg)"' in more_component
    assert ':has-conversation-data-file="hasConversationDataFile"' in more_component
    assert ':trace-id="focusedOutputTraceId"' in source
    assert 'openReusableResults(msg.reusableResultStatus?.resultId, msg.trace_id, msg.reusableResultStatus?.status)' in source
    assert '@open-artifacts="openMessageArtifacts(msg.trace_id)"' in source
    assert "const openMessageArtifacts = (traceId?: string | null) =>" in source
    assert "MessageArtifactsDrawer" not in source
    assert "const hasConversationDataFile = computed(() =>" in source
    assert "Object.values(artifactCountByTrace.value).some" in source
    assert "hasDataOutput: Boolean(item.has_data_output)" in source
    assert "reusable_result_id" in source
    assert "reusable_result_status" in source
    assert "conversationReusableResultCount" in source
    assert '<div class="hidden sm:block' in source
    action_source = source[source.index("<!-- Agent Message Actions"):]
    more_pos = action_source.index('mode="more"')
    regenerate_pos = action_source.index('@click="regenerate"')
    data_pos = action_source.index('mode="data"')
    copy_pos = action_source.index('@click="copyMessage')
    assert copy_pos < regenerate_pos < data_pos
    assert regenerate_pos < action_source.index("<ChatBIContinueAnalysis") < more_pos
    assert regenerate_pos < action_source.index("<MessageContinueAnalysis") < more_pos
    assert action_source.index("<!-- Time -->") < copy_pos
    assert "@select-reusable-result" in source
    assert "openReusableResults(" in source
    assert "<ReusableResultNotice" in source
    assert "msg.reusableResultStatus?.status === 'reused'" in source


def test_reusable_result_details_are_limited_to_the_current_message_round():
    source = LIST.read_text(encoding="utf-8")

    assert "scope?: 'conversation' | 'message'" in source
    assert "props.scope === 'message'" in source
    assert "traceId?: string | null" in source
    assert "messageItems" in source
    assert "displayedItems" in source
    assert "item.trace_id === props.traceId" in source
    assert "item.result_id === props.focusedResultId" in source
    assert "本次生成" in source
    assert "本次复用" in source
    drawer_source = DRAWER.read_text(encoding="utf-8")
    assert "本会话全部" in drawer_source
    assert "本次消息" in drawer_source
    assert "查看本会话全部" not in source


def test_file_details_are_loaded_by_selected_output_scope():
    source = DRAWER.read_text(encoding="utf-8")

    assert "conversation_id: props.conversationId" in source
    assert "trace_id: outputScope.value === 'message' ? props.traceId || undefined : undefined" in source
    assert "本次消息" in source


def test_file_type_filter_includes_markdown_artifacts():
    source = DRAWER.read_text(encoding="utf-8")

    assert "{ value: 'markdown', label: 'Markdown' }" in source
    assert "artifact_type: activeType.value || undefined" in source


def test_output_lists_show_source_trace_for_conversation_scope():
    drawer_source = DRAWER.read_text(encoding="utf-8")
    result_source = LIST.read_text(encoding="utf-8")

    for source in (drawer_source, result_source):
        assert "formatTraceId" in source
        assert "来源消息" in source
        assert "trace_id" in source
        assert "当前消息" in source


def test_history_reload_restores_reusable_result_count_by_trace():
    source = EMBED.read_text(encoding="utf-8")

    assert "reusableResultCountByTrace" in source
    assert "traceResultCount" in source
    assert "counts[traceId]" in source
    assert "conversationReusableResultCount.value = items.length" in source


def test_output_availability_failures_clear_stale_session_counts():
    source = EMBED.read_text(encoding="utf-8")

    assert "conversationReusableResultCount.value = 0" in source
    assert "reusableResultCountByTrace.value = {}" in source
    assert "artifactCountByTrace.value = {}" in source
    assert "if (conversationId.value === cid)" in source


def test_embed_chat_clears_selection_after_request_snapshot_is_captured():
    source = EMBED.read_text(encoding="utf-8")

    assert "reusableResultId?: string | null" in source
    assert "selectedReusableResultId.value = null" in source
    assert "snapshot.reusableResultId" in source


def test_embed_chat_keeps_reused_notice_when_followup_result_is_saved():
    source = EMBED.read_text(encoding="utf-8")

    assert "data.status === \"saved\"" in source
    assert "msg.reusableResultStatus?.status === \"reused\"" in source
    assert "return true;" in source
