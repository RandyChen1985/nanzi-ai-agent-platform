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
    assert "agentForm.is_system ? 'border-blue-200 bg-blue-50 text-blue-700'" in drawer_source
    assert "系统预置智能体，防止误删并提高路由权重" in drawer_source
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
    assert "版本管理" not in management
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
    assert "showAgentCenterGuide" in management
    assert "dismissAgentCenterGuide" in management
    assert "batchMode" in management
    assert "batchSetEnabled" in management
    assert "toggleAgentSelection" in management
    # 复选框只在 change 时 toggle 一次，避免 click+change 双触发导致勾不上
    assert '@click.stop="toggleAgentSelection(agent.id)"' not in management
    assert '@change="toggleAgentSelection(agent.id)"' in management
    assert "待完善：" in management
    assert "完善配置" in management


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
    assert '<template #header-extra>' in management
    assert '<template #footer>' in management
    assert "系统智能体" in management
    assert "系统预置" in management
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
