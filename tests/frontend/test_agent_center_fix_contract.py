"""智能体中心本轮修复的回归契约。

这里只锁「已经核实会坏」的结构，不重复测纯样式：

* 历史抽屉超过 100 条时，提示条与时间线必须同属一个 ``v-else`` 分支；
* 就绪气泡必须认后端真实枚举，并覆盖 CHATBI / 知识库的工具项；
* 继续向导必须重新出现「智能体信息」步骤；
* 版本抽屉发布、删除后必须让父列表重新拉取；
* 分享与审核搜索必须用真实字段；
* 版本 Diff 必须包含「允许切换模型」；
* 列表视图必须与网格视图一样带专家团徽标；
* 两个常驻弹窗的异步请求必须带序号保护，转交弹窗关闭时要清掉防抖计时器。
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _block(source: str, start_marker: str, end_marker: str = "\nconst ") -> str:
    start = source.index(start_marker)
    end = source.find(end_marker, start + len(start_marker))
    return source[start:] if end == -1 else source[start:end]


def test_system_history_timeline_shares_v_else_with_hint():
    source = _read("frontend/src/components/agent/AgentSystemHistoryDrawer.vue")
    marker = 'v-if="totalCount > entries.length"'
    index = source.index(marker)
    before = source[max(0, index - 160):index]
    assert "<div v-else" in before, (
        "超过 100 条时的提示条必须与 <ol> 放进同一个 v-else 容器，"
        "否则 <ol v-else> 会绑到提示条上，时间线直接消失"
    )


def test_readiness_popover_uses_backend_enum_and_tool_checks():
    source = _read("frontend/src/views/AgentManagement.vue")
    assert "published_version_missing" not in source
    assert "published_version" in source
    assert "data_query_tool" in source
    assert "knowledge_base_tool" in source


def test_onboarding_flow_keeps_agent_info_step():
    source = _read("frontend/src/views/AgentManagement.vue")
    block = _block(source, "const versionConfigSteps = computed", "\n});")
    assert "isOnboardingFlow" in block, (
        "接受分享后的继续向导也要展示「智能体信息」步骤，否则用户无法改名/换头像"
    )


def test_publish_from_drawer_refreshes_parent_list():
    source = _read("frontend/src/views/AgentManagement.vue")
    block = _block(source, "const handleDrawerPublishVersion")
    assert "fetchAgents()" in block, "抽屉里发布成功后必须刷新卡片列表的版本与就绪状态"


def test_delete_version_notifies_parent_and_user():
    source = _read("frontend/src/components/agent/AgentVersionsDrawer.vue")
    block = _block(source, "const confirmDeleteVersion")
    assert "emit('update')" in block
    assert "showToast" in block


def test_share_and_review_search_use_real_fields():
    received = _read("frontend/src/components/agent/AgentShareReceivedPanel.vue")
    sent = _read("frontend/src/components/agent/AgentShareSentPanel.vue")
    review = _read("frontend/src/components/agent/AgentSystemReviewDrawer.vue")

    assert "item.share_message" in received
    assert "item.message" not in received
    assert "item.share_message" in sent
    assert "item.requested_by_name" in review


def test_version_diff_covers_allow_model_switch():
    source = _read("frontend/src/utils/agentVersionDiff.ts")
    assert "allow_model_switch" in source


def test_list_view_renders_expert_team_badge():
    source = _read("frontend/src/views/AgentManagement.vue")
    assert source.count("👥 专家团") >= 2, "网格视图有专家团徽标，列表视图也必须有"


def test_drawer_modals_guard_stale_responses_and_timer():
    transfer = _read("frontend/src/components/agent/AgentTransferOwnerModal.vue")
    unpublish = _read("frontend/src/components/agent/AgentSystemUnpublishModal.vue")

    assert "seq === loadUsersSeq" in transfer
    assert "onBeforeUnmount" in transfer
    assert "clearTimeout(searchTimer)" in transfer
    assert "seq === previewSeq" in unpublish


def test_readiness_error_reads_structured_payload():
    """全局异常处理器把结构化 detail 放在 data 里，前端不能只读 detail。"""
    for path in (
        "frontend/src/components/agent/AgentVersionsDrawer.vue",
        "frontend/src/views/AgentManagement.vue",
        "frontend/src/components/agent/AgentSystemReviewDrawer.vue",
    ):
        source = _read(path)
        assert "readiness" in source, path
        assert "AGENT_NOT_READY" in source, path


def test_onboarding_sync_merges_dataset_ids_and_locks_engine_switch():
    """继续向导保存元数据时要合并 dataset_ids，且不允许切换已落库副本的执行引擎。"""
    mgmt = _read("frontend/src/views/AgentManagement.vue")
    sync_block = _block(mgmt, "const syncOnboardingAgentMeta")
    assert "engineConfigUI.value.dataset_ids" in sync_block
    assert "dataset_ids: datasetIds" in sync_block

    continue_block = _block(mgmt, "const continueAgentOnboarding")
    assert "dataset_ids: Array.isArray" in continue_block, (
        "继续向导必须把 dataset_ids 数组也复制出来，避免编辑器原地修改污染列表数据"
    )

    drawer = _read("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
    engine_block = _block(drawer, "const selectEngine")
    assert "if (!props.isCreatingAgent) return;" in engine_block
    assert "执行引擎在创建后不可修改" in drawer
