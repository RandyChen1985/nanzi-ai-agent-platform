from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ENDPOINT = ROOT / "app/api/portal/endpoints/system.py"
OVERVIEW = ROOT / "frontend/src/views/Overview.vue"
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"
CHECKLIST = ROOT / "frontend/src/components/system/DeploymentChecklist.vue"


def test_system_endpoint_exposes_admin_deployment_checklist():
    source = SYSTEM_ENDPOINT.read_text(encoding="utf-8")

    assert "/setup-checklist" in source
    assert "require_admin" in source
    assert "post_install_v2" in source
    assert "model_config" in source
    assert "system_config" in source
    assert "agent_config" in source
    assert "knowledge_environment" in source


def test_deployment_checklist_is_admin_only_and_reachable_from_overview():
    overview = OVERVIEW.read_text(encoding="utf-8")
    system_config = SYSTEM_CONFIG.read_text(encoding="utf-8")
    checklist = CHECKLIST.read_text(encoding="utf-8")

    assert "userInfo?.role === 'admin'" in overview
    assert "DeploymentChecklist" in overview
    assert "DeploymentChecklist" not in system_config
    assert "标记为完成" in checklist
    assert "前往配置" in checklist
    assert "isExpanded" in checklist
    assert "isComplete" in checklist
    assert "重新展开" in checklist
    assert "hideWhenComplete" in checklist
    assert "部署检查已完成" in checklist
    assert "v-else-if=\"!loading && visible && props.compact\"" in checklist
    assert ">部署检查</span>" in checklist
    assert "border-0 bg-transparent" in checklist
    assert "&& isExpanded" in checklist
    assert "deployment-checklist-details" in checklist
    assert "!props.compact && !loading" in checklist
    assert "检查项目" in checklist
    assert "操作步骤" in checklist
    assert "完成标准" in checklist
    assert "showHelp" in checklist
    assert "配置模型管理" in checklist
    assert "检查参数配置" in checklist
    assert "发布智能体配置" in checklist
    assert "知识库环境" in checklist
    assert "可选" in checklist
    assert "knowledge_environment" in checklist
    assert "RAGFlow" in checklist
    for item in (
        "download_url_prefix", "llm_model_name", "multimodal_model_name",
        "embed_api_url", "metadata_provider", "knowledge_ragflow_api_url",
        "sandbox_policy", "TASK_SCHEDULER_ENABLED",
    ):
        assert item in checklist
    assert "lg:flex-row" in checklist
    assert "deployment-checklist-connector" in checklist
    assert "→" in checklist
    assert "hidden lg:block" in checklist
    assert "nanzi_deployment_checklist" not in checklist
