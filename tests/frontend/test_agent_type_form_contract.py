from pathlib import Path
import pytest

pytestmark = pytest.mark.no_infrastructure


def test_agent_form_uses_fixed_primary_types_and_locked_capabilities():
    source = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "AGENT_TYPE_OPTIONS" in source
    assert 'value: "GENERAL"' in source
    assert 'value: "CHATBI"' in source
    assert 'value: "KNOWLEDGE_BASE"' in source
    assert "lockedCapabilityForType" in source
    assert "输入能力并回车" not in source


def test_agent_api_exposes_primary_type_contract():
    source = Path("frontend/src/api/agent.ts").read_text()

    assert "export type AgentType = 'GENERAL' | 'CHATBI' | 'KNOWLEDGE_BASE'" in source
    assert "agent_type: AgentType" in source


def test_agent_creation_reuses_version_drawer_with_agent_step():
    source = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer_source = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()
    api_source = Path("frontend/src/api/agent.ts").read_text()

    assert "startAgentCreation" in source
    assert "isCreatingAgent" in source
    assert "continueAgentOnboarding" in source
    assert 'v-if="showAgentModal && isEditingAgent"' in source
    assert "type VersionConfigStep = 'agent' | 'model'" in drawer_source
    assert "智能体信息" in drawer_source
    assert "物理标识符" in drawer_source
    assert "通用助手" in drawer_source
    assert "ChatBI" in drawer_source
    assert "知识库助手" in drawer_source
    assert "排序权重" in drawer_source
    assert "执行引擎" in drawer_source
    assert "NanZi Engine" in drawer_source
    assert "RAGFlow" in drawer_source
    assert "OpenClaw" in drawer_source
    assert "https://api.openclaw.example.com" in drawer_source
    assert "bot-123" in drawer_source
    assert "系统智能体" in drawer_source
    assert "Admin Only" in drawer_source
    # 系统智能体开关改为独立配置栏，激活态仍为蓝色高亮
    assert ':class="agentForm.is_system' in drawer_source
    assert "'border-blue-200 bg-blue-50" in drawer_source
    # 旧提示“系统预置智能体，防止误删并提高路由权重”改写为内置与委派说明
    assert "平台官方内置智能体" in drawer_source
    assert "只有系统智能体才能被自动委派" in drawer_source
    assert "扩展能力标签" in drawer_source
    assert "系统内置标签" in drawer_source
    assert "lockedPrimaryCapability" in drawer_source
    assert "showCapabilityHelp" in drawer_source
    assert "扩展能力标签怎么用" in drawer_source
    assert 'showCapabilityHelp = !showCapabilityHelp' not in drawer_source
    assert "如何影响路由" in drawer_source
    assert "contract_review" in drawer_source
    assert "只影响路由和委派" in drawer_source
    assert "showAgentTypeHelp" in drawer_source
    assert "智能体类型怎么选" in drawer_source
    assert "查看智能体类型说明" in drawer_source
    assert 'showAgentTypeHelp = !showAgentTypeHelp' not in drawer_source
    assert "showEngineHelp" in drawer_source
    assert "执行引擎怎么选" in drawer_source
    assert "查看执行引擎说明" in drawer_source
    assert "z-[10000]" in drawer_source
    assert "创建保存后不可修改" in drawer_source
    assert "未显式绑定时，自动使用当前用户有权访问的数据集" in drawer_source
    assert "isCreatingAgent" in drawer_source
    assert "当前类型" in drawer_source
    assert "createAgentOnboarding" in api_source


def test_unfinished_onboarding_can_publish_from_unified_version_drawer():
    source = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer_source = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

    assert ':is-onboarding-flow="isOnboardingFlow"' in source
    assert '@publish="publishVersionFromEditor"' in source
    assert "publishVersionFromEditor" in source
    assert "agentApi.publishVersion" in source
    assert "isOnboardingFlow: boolean" in drawer_source
    assert "publish: []" in drawer_source
    assert "保存并发布" in drawer_source


def test_agent_action_labels_describe_editing_and_publishing():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()
    versions_drawer = Path("frontend/src/components/agent/AgentVersionsDrawer.vue").read_text()

    assert "编辑智能体" in management
    assert "配置与发布" in management
    assert "配置与发布" in versions_drawer
    assert "配置元数据" not in management
    # “版本管理”只允许出现在指引说明里，卡片主操作标签必须是“配置与发布”
    action_label_fn = management[
        management.index("const getPrimaryCardActionLabel"):management.index("const runPrimaryCardAction")
    ]
    assert "版本管理" not in action_label_fn
    assert "配置与发布" in action_label_fn
    assert "版本管理" not in versions_drawer


def test_agent_center_filters_and_labels_cards_by_primary_type():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert 'value="GENERAL">智能体类型：通用助手' in management
    assert 'value="CHATBI">智能体类型：ChatBI' in management
    assert 'value="KNOWLEDGE_BASE">智能体类型：知识库助手' in management
    assert "a.agent_type || 'GENERAL'" in management
    assert "getAgentTypeLabel(agent)" in management
    assert "getAgentTypeBadgeClass(agent)" in management
    assert "return '通用助手'" in management
    assert "return '知识库助手'" in management


def test_agent_center_card_ux_hierarchy_and_primary_cta():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "getPrimaryCardAction" in management
    assert "getPrimaryCardActionLabel" in management
    assert "runPrimaryCardAction" in management
    assert "followReadinessGap" in management
    assert "formatReadinessMissing" in management
    assert "line-clamp-2 min-h-[2.5rem]" in management
    assert "grayscale opacity-60" in management
    assert "更新于 {{ formatDate(agent.updated_at) }}" in management
    assert "调用 {{ agent.execution_count ?? 0 }} 次" in management
    assert ">工具</span>" in management or "font-normal\">工具</span>" in management
    assert ">技能</span>" in management or "font-normal\">技能</span>" in management
    assert "formatSkillCountLabel" in management
    assert 'return "全部"' not in management
    assert "使用全部公共技能（${agent.skill_count ?? 0} 个，另含个人技能）" in management
    assert "showAgentFlowGuide" in management
    assert "dismissAgentFlowGuide" in management
    assert "batchMode" in management
    assert "batchSetEnabled" in management
    assert "toggleAgentSelection" in management
    # 复选框只在 change 时 toggle 一次，避免 click+change 双触发导致勾不上
    assert '@click.stop="toggleAgentSelection(agent.id)"' not in management
    assert '@change="toggleAgentSelection(agent.id)"' in management
    assert "待完善：" in management
    assert "完善配置" in management


def test_main_agent_is_fixed_enabled_and_not_deletable_in_management():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "isMainAgent" in management
    assert "主助手不可禁用" in management
    assert "主专家自动委派" in management
    assert "固定启用" in management
    assert ':disabled="isMainAgent(agent)"' in management
    assert 'v-if="agent.is_editable !== false && !isMainAgent(agent)"' in management
    assert '!agent.is_system && !isMainAgent(agent)' in management


def test_agent_list_keeps_continue_configuration_action_horizontal():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "min-w-[1100px]" in management
    assert "min-w-[17rem]" in management
    assert "shrink-0 items-center whitespace-nowrap" in management
    assert "getPrimaryCardActionLabel(agent)" in management
    assert "系统内置" in management
    assert "自定义" in management

def test_agent_wizard_blocks_later_steps_until_prior_complete():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

    assert "canReachVersionConfigStep" in management
    assert "isVersionConfigStepComplete" in management
    assert "handleVersionConfigStepChange" in management
    assert "请先完善智能体信息：填写物理标识符和显示名称" in management
    assert "canReachVersionConfigStep" in drawer
    assert "请先完成前面的配置步骤" in drawer
    assert "disabled:cursor-not-allowed disabled:bg-gray-300" in drawer


def test_knowledge_base_agent_blocks_tools_step_without_tool_or_binding():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

    assert "isKnowledgeBaseToolsStepComplete" in management
    assert "getKnowledgeBaseDatasetIds" in management
    assert "getEnabledToolNames" in management
    assert "getActiveAgentType" in management
    assert "if (step === 'tools') return isKnowledgeBaseToolsStepComplete();" in management
    assert "search_knowledge_base" in management
    assert "请先完善工具能力：选择知识库检索工具" in management
    assert "请先完善工具能力：为知识库检索工具绑定至少一个知识库" in management
    assert "knowledgeBaseToolsStepIssues" in management
    assert "versionConfigIncompleteHint" in management
    assert ":version-config-incomplete-hint=\"versionConfigIncompleteHint\"" in management
    assert ":knowledge-base-tools-step-issues=\"knowledgeBaseToolsStepIssues\"" in management
    assert "知识库助手还需完成以下配置，才能进入下一步" in drawer
    assert "去绑定知识库" in drawer
    assert "versionConfigIncompleteHint" in drawer
    assert "tool-action-btn--attention" in drawer

    assert "CHATBI_TOOL_GROUP_LABEL" in management
    assert "getDefaultCollapsedStaticGroups" in management
    assert "applyKnowledgeBaseToolGroupDefaults" in management
    assert "collapsed.add(CHATBI_TOOL_GROUP_LABEL)" in management

    management = Path("frontend/src/views/AgentManagement.vue").read_text()
    list_section = management.split("<!-- List View -->", 1)[1]

    assert 'class="flex w-full flex-col items-start gap-1"' in list_section
    assert "getAgentTypeLabel(agent)" in list_section
    assert "NanZi Engine" in list_section
    assert "w-36 min-w-[9rem]" in list_section
    assert "w-20 min-w-[5rem]" in list_section
    assert 'text-center w-32">状态' not in list_section


def test_agent_edit_dialog_is_compact_and_locks_engine_type_only():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()
    modal = Path("frontend/src/components/Modal.vue").read_text()
    edit_dialog = management[management.index('v-if="showAgentModal && isEditingAgent"'):]

    assert 'size="max-w-4xl"' in edit_dialog[:300]
    # 系统智能体标记从 Modal 的 header-extra 槽位移入弹窗主体配置栏
    assert "设为系统智能体" in management
    assert '<template #footer>' in management
    assert "系统智能体" in management
    assert "平台官方内置智能体" in management
    assert "排序权重 (Sort Order)" not in edit_dialog[:5000]
    assert 'title="仅影响聊天页面的智能体选择列表顺序，值越大越靠前"' in management
    assert "执行引擎不可修改" in management
    assert ':disabled="isEditingAgent"' in management
    assert "🔒 当前类型" in management
    assert "当前类型：" not in edit_dialog[:6000]
    assert "showAdvancedSafety" in management
    assert "高级安全设置" in management
    assert "高级能力设置" not in edit_dialog
    assert "扩展能力标签" in edit_dialog
    assert "系统内置标签" in edit_dialog
    assert "showCapabilityHelp" in management
    assert "查数工具必需，数据集可选" in management
    assert 'v-model="engineConfigUI.base_url"' in management
    assert 'v-model="engineConfigUI.app_id"' in management
    assert 'v-model="engineConfigUI.model"' in management
    assert '<slot name="header-extra"></slot>' in modal
    assert '<slot name="footer"></slot>' in modal
    assert "max-h-[calc(100vh-2rem)]" in modal
    assert "min-h-0 flex-1" in modal


def test_onboarding_columns_are_in_v103_migration():
    v103 = Path("db-prod/V103-add-agent-primary-type.sql").read_text()

    assert "ADD COLUMN `onboarding_key`" in v103
    assert "ADD COLUMN `onboarding_step`" in v103
    assert "uk_ai_agents_owner_onboarding" in v103
    assert v103.count("ALTER TABLE `ai_agents`") >= 4
    assert not Path("db-prod/V104-add-agent-onboarding-columns.sql").exists()


def test_creation_flow_is_engine_first_and_external_engines_skip_versions():
    source = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

    assert "isLocalCreationEngine" in source
    assert "createExternalEngineAgent" in source
    assert "agentApi.createAgent(" in source
    assert "外部引擎不创建本地版本" in source
    assert "selectEngine" in drawer
    assert "内置能力" in drawer
    assert "RAGFlow 远程智能体调用" in drawer
    assert "OpenClaw 远程任务执行" in drawer
    assert "supportsCapabilityTags" in drawer
    assert "supportsCapabilityTags" in source
    assert "normalizeAgentCapabilities" in source


def test_external_engine_creation_requires_parameters_before_save():
    source = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

    assert "externalCreationMissingFields" in drawer
    assert "请先填写" in drawer
    assert ':disabled="externalCreationMissingFields.length > 0"' in drawer
    assert "persistNewAgentDraft(isLocalCreationEngine.value)" in source


def test_agent_onboarding_key_works_on_plain_http():
    """crypto.randomUUID 在非 HTTPS 生产 IP 不可用，setup 阶段调用会导致整页白屏且不请求 agents。"""
    source = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "crypto.randomUUID" not in source
    assert 'from "../utils/conversationId"' in source
    assert "createUuid" in source


def test_static_tool_groups_offer_group_level_select_all():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()
    drawer = Path("frontend/src/components/agent/AgentVersionEditorDrawer.vue").read_text()

    assert "const isAllStaticGroupSelected" in management
    assert "const toggleSelectAllStatic" in management
    assert "groupedTools.value.find" in management
    assert "toggleSelectAllStatic: [label: string]" in drawer
    assert "isAllStaticGroupSelected: (label: string) => boolean" in drawer
    assert "@click.stop=\"emit('toggleSelectAllStatic', group.label)\"" in drawer
    assert "isAllStaticGroupSelected(group.label)" in drawer
    assert '@toggle-select-all-static="toggleSelectAllStatic"' in management


def test_browser_automation_tools_are_available_in_a_dedicated_group():
    management = Path("frontend/src/views/AgentManagement.vue").read_text()

    assert "浏览器自动化" in management
    for tool_name in (
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_fill",
        "browser_scroll",
        "browser_press",
        "browser_wait_for",
        "browser_select_option",
        "browser_read_visible",
        "browser_hover",
        "browser_drag",
        "browser_slider_drag",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_tabs",
        "browser_switch_tab",
        "browser_close_tab",
        "browser_upload",
        "browser_download",
        "browser_export_pdf",
        "browser_extract_table",
        "browser_handle_dialog",
        "browser_execute_js",
        "browser_check_auth",
        "browser_get_network_logs",
        "browser_get_cookies",
        "browser_set_cookies",
    ):
        assert tool_name in management
    assert "groups.browser.tools.push(tool)" in management
    assert "name.startsWith('browser_') || name.includes('browser')" in management
