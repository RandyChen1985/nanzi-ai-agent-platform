"""执行卡片前置生命周期明细契约。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_chat_timeline_keeps_original_step_counter_and_does_not_add_stage_wrapper():
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    header = _read("frontend/src/components/chat/ChatThinkingHeader.vue")

    assert ':step-count="countTimelineSteps(items)"' in timeline
    assert 'v-for="item in items"' in timeline
    assert "executionStages" not in timeline
    assert "detailCount" not in header


def test_backend_exposes_request_and_context_details_as_real_timeline_logs():
    source = _read("app/services/ai/agent_service.py")

    for helper in (
        "_build_request_validation_log",
        "_build_context_history_log",
        "_build_model_config_log",
        "_build_capability_catalog_log",
        "_build_prompt_assembly_log",
    ):
        assert helper in source

    assert '"请求校验"' in source
    assert '"会话上下文"' in source
    assert '"模型配置解析"' in source
    assert '"知识库和专家清单加载"' in source
    assert '"Prompt 组装"' in source
    assert "当前会话用户" in source
    assert "读取历史" in source
    assert "保留" in source
    assert "裁剪" in source


def test_backend_flattens_entry_expert_steps_under_preparation_parent():
    source = _read("app/services/ai/agent_service.py")

    assert '"加载入口专家配置"' in source
    assert '"校验入口专家权限"' in source
    assert '"加载目标专家配置"' not in source
    assert '"校验目标专家权限"' not in source
    assert 'event.get("id") in {"route:target_config", "route:target_permission"}' in source
    assert 'event["parent_id"] = "preparation:auth_context_capability"' in source


def test_preparation_parent_uses_auth_context_title_and_shield_icon():
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    assert "鉴权及上下文与能力准备" in timeline
    assert "parent_id" in timeline
    assert 'return "🛡️"' in timeline


def test_preparation_children_expand_by_default_but_can_be_reopened():
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    header = _read("frontend/src/components/chat/ChatThinkingHeader.vue")
    timeline_utils = _read("frontend/src/utils/processTimeline.ts")

    assert "isPreparationParent" in timeline
    assert "项准备" in timeline
    assert "bg-sky-50" not in timeline
    assert "border-sky-200" not in timeline
    assert "displayTimelineTitle" in timeline
    assert "toggleTimelineItem" in timeline
    assert "PREPARATION_TIMELINE_PARENT_ID" in timeline_utils
    assert "defaultChildrenExpandedForLog" in timeline_utils
    assert "defaultChildrenExpandedForLog(data.id)" in timeline_utils
    assert "defaultChildrenExpandedForLog(log.id)" in timeline_utils
    # 进行中默认展开由 utils 兜底；执行完成后仅在「鉴权及上下文与能力准备」父级 guard
    # 分支内折叠其子树（childrenExpanded = false），不波及 route 等其它父级。
    collapse_guard = "isPreparationParent(item) && item.children?.length"
    assert collapse_guard in timeline
    assert timeline.index("item.childrenExpanded = false") > timeline.index(collapse_guard)
    assert "childrenExpanded = false" in timeline
    assert "主专家开始处理" in timeline_utils
    assert "工具可用性检查" in timeline_utils
    assert "模型调用 ·" in timeline_utils
    assert "#0ea5e9" in timeline
    assert "#0ea5e9" in header


def test_file_tool_metadata_is_available_for_timeline_display():
    runner = _read("app/services/ai/runners/assistant_agent_runner.py")
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    process = _read("frontend/src/utils/processTimeline.ts")

    assert '"file_metadata"' in runner
    assert "file_metadata?:" in timeline
    assert "file_metadata?:" in process
    assert "metadata.path" in timeline
    assert "metadata.operation" in timeline
    assert "metadata.pattern" in timeline
    assert "const baseTitle = formatTimelineTitle(item.title || item.tool_name || \"执行步骤\")" in timeline
    assert "operationLabels" in timeline
    assert "路径：" in timeline
    assert "关键词：" in timeline
    assert "工作表：" in timeline
    assert "范围：" in timeline
    assert "metadata.changes" in timeline


def test_sse_handler_forwards_file_metadata_into_process_timeline():
    handlers = _read("frontend/src/utils/agentscopeSseHandlers.ts")
    snapshot = _read("app/services/ai/runtime/agentscope/process_timeline_snapshot.py")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert "file_metadata: data.file_metadata" in handlers
    assert "syncProcessTimelineLog" in handlers
    assert '"file_metadata"' in snapshot
    assert "file_metadata" in embed
    assert "file_metadata" in debug
    assert "file_metadata" in handlers


def test_tool_permission_card_exposes_decision_context_and_accessible_actions():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    for marker in (
        "需要确认执行",
        "影响范围",
        "风险等级",
        "命令详情",
        "复制命令",
        "已复制",
        "复制失败",
        "本次允许执行",
        "拒绝执行",
        "正在提交确认",
        "aria-live=\"polite\"",
        "aria-busy",
        "@keydown.enter",
        "@keydown.space",
        "min-h-[2.75rem]",
        "sm:flex-row",
    ):
        assert marker in card


def test_tool_permission_card_uses_adaptive_compact_layout_for_single_commands():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")

    assert "const isCompact = computed" in card
    assert 'v-if="isCompact"' in card
    assert "仅本次执行" in card
    assert "v-else" in card


def test_tool_permission_card_aligns_with_thinking_timeline_width():
    card = _read("frontend/src/components/chat/ToolPermissionCard.vue")
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")

    for width_class in (
        "w-full min-w-0 max-w-[42rem]",
        "lg:max-w-[48rem]",
        "2xl:max-w-[52rem]",
    ):
        assert width_class in timeline
        assert width_class in card


def test_permission_timeline_row_can_be_suppressed_when_detail_card_is_visible():
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert "suppressPermissionLogs" in timeline
    assert "category === \"permission\"" in timeline
    for source in (embed, debug):
        assert ':suppress-permission-logs="Boolean(msg.pendingPermission)"' in source


def test_both_chat_surfaces_use_the_shared_tool_permission_card():
    embed = _read("frontend/src/views/EmbedChat.vue")
    debug = _read("frontend/src/views/AgentDebug.vue")

    for source in (embed, debug):
        assert "import ToolPermissionCard from \"@/components/chat/ToolPermissionCard.vue\"" in source
        assert "<ToolPermissionCard" in source
        assert "@submit=\"(confirmed) => confirmPendingPermission(msg, confirmed)\"" in source
        assert "<!-- Tool Permission Confirmation -->" not in source


def test_execution_timeline_keeps_error_reason_alongside_prewarm_progress():
    timeline = _read("frontend/src/components/chat/ChatExecutionTimeline.vue")
    # A 档在子行插入沙箱预热进度提示时，不能挤占原有"错误原因"展示（回归保护）。
    assert 'v-if="child.error_reason"' in timeline
    assert "错误原因：{{ child.error_reason }}" in timeline
    assert "if (walk(child)) return true;" in timeline
    assert "fileMetadataSummary(child.file_metadata)" in timeline
