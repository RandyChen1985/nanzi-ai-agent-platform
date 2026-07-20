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
    assert "System Agent" in drawer_source
    assert "系统预置智能体，防止误删并提高路由权重" in drawer_source
    assert "扩展能力标签" in drawer_source
    assert "系统内置标签" in drawer_source
    assert "lockedPrimaryCapability" in drawer_source
    assert "showCapabilityHelp" in drawer_source
    assert "如何影响路由" in drawer_source
    assert "contract_review" in drawer_source
    assert "只影响路由和委派" in drawer_source
    assert "showAgentTypeHelp" in drawer_source
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
    assert "v-if=\"agentForm.engine_type === 'LOCAL'\"" in drawer
