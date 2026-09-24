from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_embed_portal_contract(source: str) -> None:
    assert "useDatasetPortal" in source
    assert "DatasetPortalDrawer" in source
    assert "const datasetMenuLoading = ref(false);" in source
    assert "DATASET_PORTAL_SLASH_COMMAND" in source
    assert "isDatasetPortalSlashCommand" in source
    assert "await openPortalDrawer();" in source
    assert "DatasetCapabilityMenu" in source
    assert "datasetNavigation?: DatasetNavigationPayload;" in source
    assert "findUniqueDataQueryAgent" in source
    assert "matches.length === 1" in source
    assert "listKnowledgeExpertAgents" in source
    assert "resolveKnowledgeExpertAgent" in source
    assert "lockToDataQueryAgentForDatasetMenu" not in source
    assert "switchToAutoRouting" not in source
    assert "capabilities.includes(\"knowledge_base\")" in source
    assert "capabilities.includes(\"data_query\")" in source
    assert "refreshDatasetMenuNavigation" in source
    assert "recordDatasetMenuQuestionClick" in source
    assert "dataset_menu_hash" in source
    # 数据门户快捷指令标签已按 d1fd032d 去除多余 Emoji，改为纯文本「数据门户」
    assert 'command: DATASET_PORTAL_SLASH_COMMAND, label: "数据门户"' in source
    assert "embed_portal_keep_open" in source
    assert "onPortalLoadingChange" in source
    assert "applyPortalViewportLayout" in _source("frontend/src/composables/useDatasetPortal.ts")
    assert "lockToDataQueryAgentForDatasetMenu" not in _source("frontend/src/composables/useDatasetPortal.ts")
    assert "switchToAutoRouting" not in _source("frontend/src/composables/useDatasetPortal.ts")


def _assert_agent_debug_portal_contract(source: str) -> None:
    assert "useDatasetPortal" in source
    assert "DatasetPortalDrawer" in source
    assert "DATASET_PORTAL_SLASH_COMMAND" in source
    assert "isDatasetPortalSlashCommand" in source
    assert "await openPortalDrawer();" in source
    assert "DatasetCapabilityMenu" in source
    assert "datasetNavigation?: DatasetNavigationPayload;" in source
    assert "findUniqueDataQueryAgent" in source
    assert "matches.length === 1" in source
    assert "listKnowledgeExpertAgents" in source
    assert "resolveKnowledgeExpertAgent" in source
    assert "lockToDataQueryAgentForDatasetMenu" not in source
    assert "switchToAutoRouting" not in source
    assert "refreshDatasetMenuNavigation" in source
    assert "recordPortalQuestionClick" in source


def test_embed_chat_locks_input_while_dataset_menu_loads():
    _assert_embed_portal_contract(_source("frontend/src/views/EmbedChat.vue"))


def test_agent_debug_locks_input_while_dataset_menu_loads():
    _assert_agent_debug_portal_contract(_source("frontend/src/views/AgentDebug.vue"))


def test_dataset_capability_menu_component_contract():
    source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    assert "defineEmits" in source
    assert "quick-question" in source
    assert "record-question-click" in source
    assert "refresh" in source
    assert "payload.generated_at" in source
    assert "payload.dataset_menu_hash" in source
    assert "payload.from_cache" in source
    assert "payload.has_datasets" in source
    assert "isNoPermissionEmpty" in source
    assert "showStatusBanner" in source
    assert "refreshDisabled" in source
    assert "props.initialLoading" in source
    assert "showRefreshBusy.value" in source
    assert "formatGroupUpdatedAt" in source
    assert "更新 {{ formatGroupUpdatedAt(group) }}" in source
    assert "updated_at?: string" in source
    assert "|| props.payload?.has_datasets === false" not in source
    assert "cacheAgeLabel" in source
    assert "数据门户" in source
    assert "click_count" in source
    assert "handleQuestionClick" in source
    assert "props.payload.groups" in source
    assert "group.questions" in source
    assert "group.followups" in source
    assert "related_data" in source
    assert "GROUP_REFRESH_COOLDOWN_MS" in source
    assert "startGroupRefreshCooldown" in source
    assert "换一批太频繁，请稍后再试" in source
    assert "暂无更多不同问题，稍后再试" in source
    assert "QUESTIONS_SECTION_TIP" in source
    assert "FOLLOWUPS_SECTION_TIP" in source
    assert "该场景的入门示例问题" in source
    assert "延伸探索型追问" in source
    assert "params_schema" in source
    assert "default_params" in source
    assert "analysis_mode" in source
    assert "savedReportScope" in source
    assert "selectedSavedReportTag" in source
    assert "openShareReportModal" in source
    assert "/shares" in source
    assert "/copy" in source
    assert "filteredSavedReports" in source
    assert "fetchShareCandidates" in source
    assert "/api/portal/management/users" in source
    assert "/api/portal/roles" in source
    assert "toggleShareUser" in source
    assert "toggleShareRole" in source
    assert "selectedShareUserIds" in source
    assert "selectedShareRoleIds" in source
    assert "shareUserSearch" in source
    assert "shareRoleSearch" in source
    assert "extractSavedReportActionErrorMessage" in source
    assert "responseData?.message" in source
    assert "暂无该报表所需数据权限，无法复制" in source
    assert "Request failed with status code" in source
    assert "showToast(extractSavedReportActionErrorMessage(error, \"复制失败\"), \"error\")" in source
    assert "run_permission_status" in source
    assert "getSavedReportRunPermissionLabel" in source
    assert "getSavedReportRunPermissionClass" in source
    assert "report.run_permission_status === \"denied\"" in source
    assert "SavedReportItemCard" in source
    assert "isSavedReportActionDisabled" in source
    assert "isSavedReportActionDisabled" in source
    assert "getSavedReportCopyTitle" in source
    assert "暂无该报表所需数据权限，无法复制。" in source
    assert "无数据权限" in source
    assert "share_summary" in source
    assert "getShareTargetLabel" in source
    assert "已共享给" in source
    assert "showSavedReportDetailDrawer" in source
    assert "openSavedReportDetail" in source
    detail_handler = source.split("const openSavedReportDetail", 1)[1].split("const openSavedReportSubscription", 1)[0]
    assert "showSavedReportBrowser.value = false" in detail_handler
    assert "toggleSavedReportFavorite" in source
    assert "toggleSavedReportPinned" in source
    assert "savedReportSmartFilter" in source
    assert "置顶" in source
    assert "收藏" in source
    assert "常用" in source
    assert "/prefs" in source
    assert "报表详情" in source
    assert "sortSavedReportsForUser" in source
    assert "openSavedReportBrowser" in source
    assert "SavedReportBrowseModal" in source
    assert "放大浏览" in source
    assert "searchQuery" in _source("frontend/src/components/chatbi/SavedReportBrowseModal.vue")
    assert "browserScope" in _source("frontend/src/components/chatbi/SavedReportBrowseModal.vue")
    assert "savedReportBrowseModalRef" in source


def test_saved_report_detail_uses_read_only_sql_code_viewer():
    detail = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    viewer = _source("frontend/src/components/data-portal/SqlCodeViewer.vue")

    assert "SqlCodeViewer" in detail
    assert detail.count("<SqlCodeViewer") >= 2
    assert "basicSetup" in viewer
    assert "sql()" in viewer
    assert "EditorState.readOnly.of(true)" in viewer
    assert "EditorView.editable.of(false)" in viewer
    assert "onBeforeUnmount" in viewer


def test_saved_report_card_shows_recent_run_and_user_run_count():
    card = _source("frontend/src/components/chatbi/SavedReportItemCard.vue")

    assert "user_last_run_at" in card
    assert "last_success_at" in card
    assert "user_run_count" in card
    assert "最近运行" in card
    assert "运行次数" in card


def test_saved_report_parameterized_execution_contract():
    source = _source("frontend/src/views/EmbedChat.vue")
    editor = _source("frontend/src/components/data-portal/DataPortalReportCreateModal.vue")
    run_modal = _source("frontend/src/components/chat/SavedReportRunModal.vue")
    workflow = _source("frontend/src/composables/chat/useSavedReportWorkflow.ts")
    assert "showReportRunModal" in source
    assert "reportRunForm" in source
    assert "executeSavedReportWithOptions" in source
    assert "analysis_mode: 'auto'" in source
    assert "defer_analysis: true" in source
    assert "/analyze" in source
    assert "composeSavedReportExecuteMarkdown" in source
    assert "extractColumnMetaFromAgentMessage" in source
    assert "params: buildSavedReportRunParams(pendingSavedReport.value, reportRunForm.value)" in source
    assert "handleQuickQuestion(\"请基于刚才黄金报表结果做业务解读，指出关键结论、异常点和后续建议。\")" not in source
    assert "const shouldAutoAnalyze = true" not in source
    assert "mode: parameterSchema.length ? 'param_sql' : 'static_sql'" in editor
    assert "sql_template: parameterSchema.length ? sqlContent : undefined" in editor
    assert "tags: tagList" in editor
    assert "description: form.value.description.trim() || undefined" in editor
    assert "\\d{2}:\\d{2}:\\d{2}" in workflow
    assert "start_datetime" in workflow
    assert "end_datetime" in workflow
    assert "month_range" in source
    assert "start_month" in source
    assert "end_month" in source
    assert "last_6_completed_months" in source
    assert "customParams" in workflow
    assert "usesDateRange" in run_modal
    assert "参数配置" in run_modal
    assert "type=\"month\"" in run_modal
    assert "extractSavedReportExecuteErrorMessage" in source
    assert "responseData?.message" in workflow
    assert "暂无该报表所需数据权限" in workflow
    assert "Request failed with status code" in workflow
    assert "previewSavedReportRun" in source
    assert "/preview" in source
    assert "reportRunPreview" in source
    assert "实际执行 SQL" in run_modal
    # 消息底部「添加固化报表」入口已抽到 MessageActionMenus 子组件（见 c1d5f563），
    # EmbedChat 侧保留 save-report 事件接线作为行为锚点。
    assert '@save-report="handleSaveReportFromMessage(msg)"' in source
    assert "savedReportNeedsRunOptions" in source
    assert "scheduleSavedReportPreview" in source
    assert "请等待运行预览完成后再执行" in source
    assert "!reportRunPreview" in source


def test_row_permission_notice_renders_above_chatbi_results():
    embed_source = _source("frontend/src/views/EmbedChat.vue")
    debug_source = _source("frontend/src/views/AgentDebug.vue")
    react_stream_source = _source("app/services/ai/runners/chatbi/react_stream.py")
    data_api_source = _source("app/services/ai/tools/data_api.py")

    for source in (embed_source, debug_source):
        assert "interface PermissionNotice" in source
        assert "permissionNotice?: PermissionNotice;" in source
        assert "msg.permissionNotice?.row_filter_applied" in source
        assert "已按你的数据权限自动过滤结果" in source
        assert "agentMsg.value.permissionNotice = execResult?.permission_notice" in source
        assert "data.permission_notice" in source

    assert '"type": "meta", "permission_notice": notice' in react_stream_source
    assert '"row_filter_applied": True' in react_stream_source or 'log_payload["row_filter_applied"] = True' in react_stream_source
    assert "msg.permissionNotice?.row_filter_applied" in embed_source
    assert "msg.permissionNotice?.row_filter_applied" in debug_source
    assert "resolve_executed_sql_for_tool_log" in _source("app/services/ai/runners/chatbi/tool_result_handlers.py")
    assert "attach_permission_notice_to_json_result" in data_api_source
    assert "permission_notice=permission_notice" in data_api_source


def test_saved_report_tags_are_not_raw_question_prefixes():
    embed_source = _source("frontend/src/views/EmbedChat.vue")
    debug_source = _source("frontend/src/views/AgentDebug.vue")

    for source in (embed_source, debug_source):
        assert "deriveSavedReportTagsInput" in source
        assert "tags_input: deriveSavedReportTagsInput(requirementIntent, originalQuery)" in source
        assert "originalQuery.slice(0, 12)" not in source


def test_saved_report_modal_avoids_pinned_dataset_portal_drawer():
    embed_source = _source("frontend/src/views/EmbedChat.vue")
    debug_source = _source("frontend/src/views/AgentDebug.vue")

    for source in (embed_source, debug_source):
        assert "saveReportModalOverlayClass" in source
        assert "return isPinned ? 'right-[28rem]' : 'right-0'" in source
        assert ':overlay-class="saveReportModalOverlayClass"' in source

    run_modal_source = _source("frontend/src/components/chat/SavedReportRunModal.vue")
    assert "inset-y-0 left-0" in run_modal_source
    assert ':class="overlayClass"' in run_modal_source


def test_saved_report_run_detail_renders_result_table_and_csv_export():
    menu_source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    result_component = _source("frontend/src/components/chatbi/SavedReportResultTable.vue")

    assert "SavedReportResultTable" in menu_source
    assert "result_snapshot" in menu_source
    assert "导出 CSV" in result_component
    assert "URL.createObjectURL" in result_component
    assert "column_labels" in result_component


def test_saved_report_edit_contract():
    menu_source = _source("frontend/src/components/chatbi/DatasetCapabilityMenu.vue")
    drawer_source = _source("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    embed_source = _source("frontend/src/views/EmbedChat.vue")
    debug_source = _source("frontend/src/views/AgentDebug.vue")
    editor_source = _source("frontend/src/components/data-portal/DataPortalReportCreateModal.vue")

    assert "edit-saved-report" in menu_source
    assert "handleEditReport" in menu_source
    assert "emit(\"edit-saved-report\", report)" in menu_source
    assert "@edit-saved-report=\"(p) => emit('edit-saved-report', p)\"" in drawer_source
    assert "const openEditReportModal = (report: any)" in embed_source
    assert "<DataPortalReportCreateModal" in embed_source
    assert ':report="saveReportForm"' in embed_source
    assert "const openEditReportModal = (report: any)" in debug_source
    assert "<DataPortalReportCreateModal" in debug_source
    assert ':report="saveReportForm"' in debug_source
    assert "axios.put(`/api/portal/saved-reports/${activeReport.value.id}`, payload)" in editor_source


def test_model_registry_uses_custom_delete_confirm_modal():
    source = _source("frontend/src/components/system/ModelRegistry.vue")
    assert "import ConfirmModal" in source
    assert "showDeleteConfirm" in source
    assert "pendingDeleteModel" in source
    assert "<ConfirmModal" in source
    assert "confirmDeleteModel" in source
    assert "confirm(" not in source


def test_dataset_portal_composable_contract():
    source = _source("frontend/src/composables/useDatasetPortal.ts")
    assert "PORTAL_OPEN_HOTKEY" in source
    assert "shiftKey && key === \"d\"" in source
    assert "readStoredBoolean" in source
    assert "!isMobileViewport()" in source
    assert "from_cache" in source
    assert "has_datasets !== false" in source
    assert "/api/v1/chat/dataset-menu/click" in source


def test_thought_step_dimming_contract():
    source = _source("frontend/src/utils/turnLogDisplay.ts")
    assert "isActiveThoughtStep" in source
    assert "isDimmedThoughtStep" in source
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")
    embed = _source("frontend/src/views/EmbedChat.vue")
    assert "进行中" in timeline
    # 进行中步骤用呼吸绿点强调，避免整行粗边框抢戏
    assert 'class="thought-status-dot shrink-0"' in timeline
    assert "border-l-2 border-primary/55" not in embed
    assert "border-l-2 border-primary/55" not in timeline
    assert "border border-blue-100/80 dark:border-blue-800/40 shadow-sm animate-pulse-subtle" not in embed



def test_thought_step_timer_contract():
    handlers = _source("frontend/src/utils/agentscopeSseHandlers.ts")
    assert "finalizePendingStreamLogs" in handlers
    assert "isLiveThoughtStepTimer" in handlers
    assert "findPendingAgentReplyLog" in handlers
    embed = _source("frontend/src/views/EmbedChat.vue")
    timeline = _source("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "finalizeAllPendingStreamLogs(agentMsg.value)" in embed
    assert "function formatDuration(duration?: number | null)" in timeline
    # 单卡实时秒表必须真的接线：此前 isLiveThoughtStepTimer 只被定义、无人引用，
    # 导致长耗时工具调用在执行期间那一行完全不显示耗时。
    assert "resolveLiveTimerLogId" in timeline
    assert "resolveLiveTimerDurationMs" in timeline
    assert "liveTimerLogId" in timeline
    assert "needsLiveTick" in timeline
    assert "tickNow.value" in timeline


def test_dataset_portal_drawer_pin_contract():
    source = _source("frontend/src/components/chatbi/DatasetPortalDrawer.vue")
    assert 'defineModel<boolean>("pinned"' in source
    assert "v-if=\"!pinned\"" in source
    assert "pointer-events-none" in source
    assert "钉住" in source
    assert "已钉住" in source
    assert "translate-y-full" in source
    assert "isMobile" in source
    assert 'teleport to="body"' in source
    assert "max-h-[92%]" in source
    assert "portal-drawer-scroll" in source


def test_portal_prompt_time_anchor_contract():
    source = _source("app/services/ai/executors/prompts.py")
    assert "build_data_query_time_anchor_block" in source
    assert "_portal_time_recommendation_rules" in source
    assert "dataset_navigation_generation_prompt" in source
    assert "build_group_questions_refresh_prompt" in source
    assert "build_group_followups_refresh_prompt" in source
