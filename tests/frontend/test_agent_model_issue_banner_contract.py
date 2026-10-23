"""「模型配置异常」顶部提示的契约测试。

背景见 ``docs/superpowers/specs/2026-10-22-agent-model-health-design.md``：
智能体版本里存了环境不可用的模型名时，界面上原本完全看不出来，只有运行时
才报 ``LLM API Key is missing``。提示覆盖两个来源——系统默认模型本身，
以及单个智能体绑定的模型。
"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
MANAGEMENT = ROOT / "frontend/src/views/AgentManagement.vue"
AGENT_API = ROOT / "frontend/src/api/agent.ts"


def _management_source() -> str:
    return MANAGEMENT.read_text(encoding="utf-8")


def test_banner_only_renders_when_issues_exist():
    source = _management_source()

    assert "const modelIssues = computed(() => agents.value.filter(a => a.model_issue))" in source
    assert "const toolIssues = computed(() => agents.value.filter(a => a.tool_issue))" in source
    # 横幅必须由长度守卫，没问题时不占空间
    assert 'v-if="visibleConfigIssues.length"' in source
    assert "处配置异常" in source


def test_banner_covers_system_default_model():
    source = _management_source()

    assert "const defaultModelIssue = ref<AgentModelIssue | null>(null)" in source
    assert "agentApi.getModelHealth()" in source
    assert "fetchModelHealth()" in source
    assert "'system-default'" in source
    assert "系统默认 LLM 模型" in source
    # 默认模型问题要能跳到模型管理去修
    assert "router.push('/dashboard/system?tab=models')" in source


def test_banner_tells_users_where_to_fix_instead_of_jumping():
    """智能体的模型在版本配置里，而用户可能一个版本都没建过——跳不过去也没意义。"""
    source = _management_source()

    assert "MODEL_ISSUE_HINT" in source
    assert "版本配置 → 模型" in source
    # 不能再复用基础信息弹窗（那里改不了模型）
    assert "openAgentModal(item.agent)" not in source
    assert "去修复" not in source


def test_banner_can_be_dismissed_for_the_session():
    source = _management_source()

    assert "const configIssuesDismissed = ref(false)" in source
    assert "configIssuesDismissed = true" in source
    # 问题清空后必须重置，否则修好一个再出现新问题就永远不提示了
    assert "if (!list.length) configIssuesDismissed.value = false" in source


def test_readiness_popover_uses_real_model_issue_instead_of_phantom_flag():
    """就绪度弹窗里原本判断一个后端从不返回的 model_missing，是死代码。"""
    source = _management_source()

    assert "model_missing" not in source
    assert "agent.model_issue ? agent.model_issue.message : '已就绪'" in source


def test_agent_api_type_exposes_model_issue_and_health_endpoint():
    source = AGENT_API.read_text(encoding="utf-8")

    assert "model_issue?: AgentModelIssue | null" in source
    assert "export interface AgentModelIssue" in source
    assert "/api/portal/agents/model-health" in source


def test_banner_also_surfaces_stale_tool_refs_from_published_version():
    """工具/MCP 失效与模型问题共用一条横幅，避免用户点进版本配置才发现。"""
    source = _management_source()

    # 后端在列表接口算好摘要（tool_issue），前端只合并展示
    assert "const toolIssues = computed(() => agents.value.filter(a => a.tool_issue))" in source
    assert "for (const agent of toolIssues.value)" in source
    assert "agent.tool_issue?.message" in source
    assert "「版本配置 → 工具能力」移除失效引用后重新发布" in source
    # 两类条目用前缀区分 key：同一智能体可能同时有模型问题和工具问题
    assert "key: `model:${agent.id}`" in source
    assert "key: `tool:${agent.id}`" in source
    # 类型徽章让用户一眼分清是模型还是工具
    assert "item.kind === 'tool' ? 'bg-amber-100 text-amber-700'" in source
    assert "{{ item.kind === 'tool' ? '工具' : '模型' }}" in source


def test_agent_api_type_exposes_tool_issue():
    source = AGENT_API.read_text(encoding="utf-8")

    assert "export interface AgentToolIssue" in source
    assert "tool_issue?: AgentToolIssue | null" in source
