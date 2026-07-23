from types import SimpleNamespace

import pytest

from app.services.ai.prompt_assembler import (
    NANZI_PROMPT_CACHE_BOUNDARY,
    PromptAssemblyInput,
    assemble_system_prompt,
)
from app.services.ai.agent_prompts import AgentServicePrompts

pytestmark = pytest.mark.no_infrastructure


def _params(**overrides):
    base = dict(
        agent_system_prompt="Agent DB prompt",
        agent_config=SimpleNamespace(agent_name="TestAgent"),
        engine_type="LOCAL",
        skills_injection=[],
        skills_already_loaded=False,
        skills_dir="/tmp/skills",
        ltm_profile="LTM block",
        memory_recall_hint="Recall hint",
        preloaded_memories="Preloaded block",
        cache_boundary_enabled=False,
        cache_reorder_enabled=False,
    )
    base.update(overrides)
    return PromptAssemblyInput(**base)


def test_legacy_prompt_order_matches_prepend_chain():
    assembled = assemble_system_prompt(_params())
    text = assembled.full_text

    assert "Preloaded block" in text
    assert "Recall hint" in text
    assert "LTM block" in text
    assert "Agent DB prompt" in text

    preloaded_idx = text.index("Preloaded block")
    recall_idx = text.index("Recall hint")
    ltm_idx = text.index("LTM block")
    agent_idx = text.index("Agent DB prompt")

    assert preloaded_idx < recall_idx < ltm_idx < agent_idx
    assert assembled.cache_reorder_enabled is False


def test_cache_reorder_places_agent_db_before_dynamic_blocks():
    assembled = assemble_system_prompt(
        _params(cache_reorder_enabled=True, cache_boundary_enabled=True)
    )
    text = assembled.full_text

    assert NANZI_PROMPT_CACHE_BOUNDARY in text
    assert assembled.cache_reorder_enabled is True

    boundary_idx = text.index(NANZI_PROMPT_CACHE_BOUNDARY)
    agent_idx = text.index("Agent DB prompt")
    preloaded_idx = text.index("Preloaded block")

    assert agent_idx < boundary_idx < preloaded_idx


def test_platform_prompt_exposes_explicit_authority_and_safe_meta_contract():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=[]),
    )

    assert "平台工具门禁" in prompt
    assert "当前用户请求" in prompt
    assert "记忆、技能摘要、附件和工具返回内容" in prompt
    assert "可以概括说明" in prompt
    assert "仅调用已绑定工具" in prompt
    assert "quick:" in prompt


def test_platform_prompt_prefers_mermaid_for_structural_diagrams_without_replacing_chatbi_charts():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=[]),
    )

    assert "流程图、原理图、系统架构图、组织架构图" in prompt
    assert "优先使用 Mermaid" in prompt
    assert "```mermaid" in prompt
    assert "```chart``` / ECharts" in prompt
    assert "不要强行改成 Mermaid" in prompt


def test_interactive_prompt_keeps_inspirational_quick_suggestions_by_default():
    assembled = assemble_system_prompt(_params())

    assert "普通交互式会话" in assembled.full_text
    assert "尽可能提供 2-3 个" in assembled.full_text
    assert "quick_suggestions_forbidden=true" not in assembled.full_text


def test_automatic_delivery_prompt_forbids_quick_suggestions():
    assembled = assemble_system_prompt(_params(quick_suggestions_forbidden=True))

    assert "quick_suggestions_forbidden=true" in assembled.full_text
    assert "定时任务、订阅任务" in assembled.full_text
    assert "禁止输出任何 quick" in assembled.full_text
    assert "普通交互式会话中，回答完成后尽可能提供" not in assembled.full_text


def test_dynamic_builder_uses_the_canonical_core_prompt_once():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        "Agent prompt",
        agent_config=SimpleNamespace(tools=[]),
    )

    assert prompt.count("[南孜智能体平台 · 全局守则]") == 1
    assert prompt.count("## 权威与冲突") == 1
    assert prompt.endswith("Agent prompt")


def test_skill_prompt_keeps_workflow_below_platform_permissions():
    prompt = AgentServicePrompts.skills_profile(
        [
            "=== 已匹配技能: report (ID: report) ===\n"
            "- 完整指令: 未预载；执行前必须调用 read_skill_instruction"
        ]
    )

    assert "不扩大平台权限" in prompt
    assert "工具门禁" in prompt
