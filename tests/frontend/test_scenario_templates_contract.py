from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_scenario_templates_use_separate_market_detail_and_install_routes():
    router = _read("frontend/src/router/index.ts")
    dashboard = _read("frontend/src/views/Dashboard.vue")
    agent_management = _read("frontend/src/views/AgentManagement.vue")
    api = _read("frontend/src/api/portal.ts")
    market = _read("frontend/src/views/ScenarioTemplates.vue")
    scenario_service = _read("app/services/scenario_template_service.py")
    detail = _read("frontend/src/views/ScenarioTemplateDetail.vue")
    install = _read("frontend/src/views/ScenarioTemplateInstall.vue")

    assert "path: 'scenario-templates'" in router
    assert "name: 'ScenarioTemplates'" in router
    assert "path: 'scenario-templates/:templateId'" in router
    assert "name: 'ScenarioTemplateDetail'" in router
    assert "path: 'scenario-templates/:templateId/install'" in router
    assert "name: 'ScenarioTemplateInstall'" in router
    assert "menu:agent_management" in router

    assert "{ name: '场景模板'" not in dashboard
    assert "/dashboard/scenario-templates" not in dashboard
    assert "activeNames: ['ScenarioTemplates', 'ScenarioTemplateDetail', 'ScenarioTemplateInstall']" not in dashboard

    assert "新建智能体" in agent_management
    assert "showCreateAgentMenu" in agent_management
    assert "更多创建方式" in agent_management
    assert "空白新建" in agent_management
    assert "从场景模板交付" in agent_management
    assert "showCreateAgentMenu = false; openAgentModal()" in agent_management
    assert "showCreateAgentMenu = false; router.push('/dashboard/scenario-templates')" in agent_management
    assert "router.push('/dashboard/scenario-templates')" in agent_management

    assert "getScenarioTemplates" in api
    assert "getScenarioTemplate" in api
    assert "getScenarioTemplateResourceOptions" in api
    assert "precheckScenarioTemplate" in api
    assert "installScenarioTemplate" in api
    assert "/api/portal/scenario-templates" in api

    assert "场景包市场" in market
    assert "按业务场景一键交付智能体" in market
    assert "templateStats" in market
    assert "bg-blue-800" in market
    assert "text-blue-50" in market
    assert "bg-white p-4 text-center shadow-sm" in market
    assert "text-blue-900" in market
    assert "bg-white/15 p-3 text-center" not in market
    assert "selectedCategory" in market
    assert "activeTab" in market
    assert "type MarketTab" in market
    assert "可交付模板" in market
    assert "categoryOptions" in market
    assert "filteredTemplates" in market
    assert "canManageDelivery" in market
    assert "menu:ai_chat" in router
    assert "meta: { perm: 'menu:ai_chat', title: '场景模板' }" in router
    assert "meta: { perm: 'menu:agent_management', title: '交付向导' }" in router
    assert "经营分析 ChatBI 助手" in scenario_service
    assert "企业知识问答助手" in scenario_service
    assert "运维巡检助手" in scenario_service
    assert "财务费用分析助手" in scenario_service
    assert "销售客户洞察助手" in scenario_service
    assert "客服工单分析助手" in scenario_service
    assert "人力制度问答助手" in scenario_service
    assert "合同法务审阅助手" in scenario_service
    assert "资源要求" in market
    assert "数据分析" in market
    assert "知识问答" in market
    assert "运维自动化" in market
    assert "查看方案" in market
    assert "router.push({ name: 'ScenarioTemplateDetail'" in market
    assert "开始交付" not in market

    assert "模板详情" in detail
    assert "业务目标" in detail
    assert "交付物" in detail
    assert "验收标准" in detail
    assert "开始交付" in detail
    assert "router.push({ name: 'ScenarioTemplateInstall'" in detail

    assert "交付向导" in install
    assert "stepFromQuery" in install
    assert "router.replace" in install
    assert "getScenarioTemplateResourceOptions" in install
    assert "resourceBindings" in install
    assert "requiredMissing" in install
    assert "canGoNext" in install
    assert "填写信息" in install
    assert "绑定资源" in install
    assert "预检安装" in install
    assert "完成交付" in install
    assert "上一步" in install
    assert "下一步" in install
    assert "交付清单" in install
    assert "没有绑定时不能安装" in install
    assert "安装记录" in install
    assert "绑定明细" in install
    assert "推荐验收问题" in install
    assert "打开调试台" in install
    assert "sample_question: installResult.value.sample_questions[0]" in install
    assert "name: 'AgentDebug'" in install
    assert "agent_id: installResult.value.agent.id" in install
    assert "version_id: installResult.value.version.id" in install
    assert "getScenarioTemplateInstances" in api
    assert "getScenarioTemplateInstance" in api
    assert "已交付场景" in market
    assert "activeTab === 'delivered'" in market
    assert "activeTab === 'templates'" in market
    assert "loadInstalledInstances" in market
    assert "instance_id: instance.id" in market
    assert "resource_summary" in market
    assert "openInstalledDebug" in market
    assert "loadInstalledInstanceResult" in install
    assert "route.query.instance_id" in install

    agent_debug = _read("frontend/src/views/AgentDebug.vue")
    assert "route.query.sample_question" in agent_debug
    assert "userInput.value = qSampleQuestion" in agent_debug


def test_get_dataset_schema_has_separate_metadata_dataset_binding_action():
    modal = _read("frontend/src/components/agent/ToolRuntimeConfigModal.vue")
    drawer = _read("frontend/src/components/agent/AgentVersionEditorDrawer.vue")
    agent_management = _read("frontend/src/views/AgentManagement.vue")
    binding_modal = _read("frontend/src/components/agent/MetadataDatasetBindingModal.vue")

    assert "metadataApi.getDatasets" not in modal
    assert "绑定元数据集" not in modal

    assert "openMetadataDatasetBinding" in drawer
    assert "tool.name === 'get_dataset_schema'" in drawer
    assert 'title="绑定数据集"' in drawer

    assert "MetadataDatasetBindingModal" in agent_management
    assert "showMetadataDatasetBindingModal" in agent_management
    assert "openMetadataDatasetBinding" in agent_management

    assert "metadataApi.getDatasets" in binding_modal
    assert "metadata_dataset_ids" in binding_modal
    assert "绑定数据集" in binding_modal
    assert "限制 get_dataset_schema" in binding_modal
