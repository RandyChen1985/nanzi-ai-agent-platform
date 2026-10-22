"""元数据智能导入向导「智能分析执行方」提示条契约测试。

覆盖四件事：
1. 前端 API 暴露只读配置查询，且路径与后端一致；
2. 向导顶部提示条区分「智能体已启用 / 未启用」两种文案，并给出配置入口；
3. 配置入口按 `menu:agent_management` 权限显隐，且不影响向导主流程（失败静默隐藏）；
4. 后端提示条口径与实际执行口径同源（禁用时不查智能体配置、提示词来源判断一致）。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_api_exposes_readonly_ai_analysis_config():
    api = (ROOT / "frontend/src/api/metadata.ts").read_text(encoding="utf-8")

    assert "getAiAnalysisConfig" in api
    assert "`${API_BASE}/ai-analysis-config`" in api
    assert "MetadataAiAnalysisConfig" in api
    # 类型必须包含提示条需要的四个关键字段
    for field in ("enabled", "model_name", "model_source", "prompt_source"):
        assert field in api


def test_wizard_banner_shows_effective_model_and_config_entry():
    source = (
        ROOT / "frontend/src/components/metadata/SmartImportWizard.vue"
    ).read_text(encoding="utf-8")

    # 两条主文案：已启用 / 未启用
    assert "本次智能分析由「" in source
    assert "当前未启用" in source
    assert "系统默认模型" in source
    assert "平台内置解析模板" in source
    # 第三种状态：已启用但读不到系统提示词时，文案必须承认仍用内置模板
    assert "但未读到该智能体的系统提示词" in source
    assert "aiAnalysisSummary" in source
    # 引导文案
    assert "如需指定模型或调整解析口径，请在智能体中心启用并配置该智能体。" in source
    assert "去配置" in source
    # 配置入口：新窗口打开，避免向导里已粘贴的 DDL 丢失
    assert "router.resolve({ name: 'AgentManagement' })" in source
    assert "window.open(target.href, '_blank', 'noopener')" in source


def test_banner_entry_is_permission_gated_and_failure_is_silent():
    source = (
        ROOT / "frontend/src/components/metadata/SmartImportWizard.vue"
    ).read_text(encoding="utf-8")

    assert "hasPermission('menu:agent_management')" in source
    assert 'v-if="canManageAgent"' in source
    # 提示条属于增强信息：读取失败不能影响导入主流程
    assert "aiAnalysisConfig.value = null" in source
    assert "提示条属于增强信息" in source


def test_backend_endpoint_uses_import_permission_and_shared_service():
    endpoints = (ROOT / "app/api/portal/endpoints/metadata.py").read_text(
        encoding="utf-8"
    )

    assert '@router.get("/ai-analysis-config"' in endpoints
    assert (
        'require_permission("element", "element:metadata:import")' in endpoints
    )
    assert "MetadataGeneratorService.describe_ai_analysis_config()" in endpoints


def test_service_keeps_same_prompt_source_rule_as_generate_from_ddl():
    service = (ROOT / "app/services/metadata_generator.py").read_text(
        encoding="utf-8"
    )

    assert "async def describe_ai_analysis_config" in service
    # 禁用态不得调用 get_active_agent_config（该方法会打 disabled 警告，提示条是高频只读查询）
    describe_block = service[
        service.index("async def describe_ai_analysis_config"):service.index(
            "async def generate_from_ddl"
        )
    ]
    assert "if enabled:" in describe_block
    assert "AgentManagerService.get_active_agent_config" in describe_block
    assert "resolve_runtime_model_info(config=agent_config)" in describe_block
    # 与真实执行分支共用同一提示词来源判断
    assert "if not agent_config or not agent_config.system_prompt" in service
    assert 'prompt_source": (' in describe_block
    assert '"agent" if agent_config and agent_config.system_prompt else "builtin"' in service
