import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.ai.prompt_assembler import (
    NANZI_PROMPT_CACHE_BOUNDARY,
    PromptAssemblyInput,
    PromptLayoutConfig,
    PromptPlan,
    assemble_system_prompt,
    resolve_prompt_layout_config,
    resolve_prompt_assembler_flags,
    resolve_effective_prompt_tool_names,
    resolve_effective_prompt_tool_names_for_turn,
    should_use_prompt_cache_layout,
)
from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.prompt_sections import PromptSection
from app.services.ai.turn_decision import TurnDecision

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_resolve_prompt_layout_config_normalizes_config_values(monkeypatch):
    values = {
        "agent_prompt_layout_mode": " ENABLED ",
        "agent_prompt_cache_rollout_percent": "100",
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    config = await resolve_prompt_layout_config()

    assert config.mode == "enabled"
    assert config.rollout_percent == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("rollout", ["-1", "101", "100.5", "125.5"])
async def test_resolve_prompt_layout_config_fails_closed_for_out_of_range_or_non_integer_rollout(
    monkeypatch, rollout
):
    async def fake_get(key, default=None):
        values = {
            "agent_prompt_layout_mode": "enabled",
            "agent_prompt_cache_rollout_percent": rollout,
        }
        return values.get(key, default)

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    config = await resolve_prompt_layout_config()

    assert config.mode == "enabled"
    assert config.rollout_percent == 0


@pytest.mark.asyncio
async def test_resolve_prompt_layout_config_fails_closed_for_invalid_values(monkeypatch):
    values = {
        "agent_prompt_layout_mode": "unsupported",
        "agent_prompt_cache_rollout_percent": "Infinity",
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    config = await resolve_prompt_layout_config()

    assert config.mode == "legacy"
    assert config.rollout_percent == 0


@pytest.mark.asyncio
async def test_resolve_prompt_layout_config_defaults_to_legacy_and_zero(monkeypatch):
    async def fake_get(key, default=None):
        return default

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    assert await resolve_prompt_layout_config() == PromptLayoutConfig()


@pytest.mark.asyncio
async def test_resolve_prompt_layout_config_reads_new_values_concurrently(monkeypatch):
    active = 0
    max_active = 0

    async def fake_get(key, default=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0)
            return {"agent_prompt_layout_mode": "enabled", "agent_prompt_cache_rollout_percent": "50"}.get(
                key, default
            )
        finally:
            active -= 1

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    assert await resolve_prompt_layout_config() == PromptLayoutConfig("enabled", 50)
    assert max_active == 2


@pytest.mark.asyncio
async def test_explicit_legacy_layout_cannot_be_enabled_by_old_boolean_flags(monkeypatch):
    values = {
        "agent_prompt_layout_mode": "legacy",
        "agent_prompt_cache_rollout_percent": "100",
        "agent_prompt_cache_boundary_enabled": "true",
        "agent_prompt_cache_reorder_enabled": "true",
    }

    async def fake_get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    config = await resolve_prompt_layout_config()

    assert await resolve_prompt_assembler_flags() == (True, True)
    assert should_use_prompt_cache_layout(config.mode, config.rollout_percent, "conversation-a") is False


def test_prompt_layout_config_is_immutable():
    config = PromptLayoutConfig()

    with pytest.raises(FrozenInstanceError):
        config.mode = "enabled"


def test_prompt_layout_uses_enabled_session_bucket_and_fails_closed_without_conversation():
    assert should_use_prompt_cache_layout("enabled", 100, "conversation-a") is True
    assert should_use_prompt_cache_layout("enabled", 0, "conversation-a") is False
    assert should_use_prompt_cache_layout("observe", 100, "conversation-a") is False
    assert should_use_prompt_cache_layout("enabled", 100, "") is False


def test_prompt_layout_session_bucket_is_deterministic():
    decisions = [
        should_use_prompt_cache_layout("enabled", 43, "conversation-deterministic")
        for _ in range(3)
    ]

    assert decisions == [decisions[0]] * 3


@pytest.mark.asyncio
async def test_resolve_prompt_assembler_flags_reads_config_concurrently(monkeypatch):
    active = 0
    max_active = 0
    values = {
        "agent_prompt_cache_boundary_enabled": "true",
        "agent_prompt_cache_reorder_enabled": "1",
    }

    async def fake_get(key, default=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0)
            return values.get(key, default)
        finally:
            active -= 1

    monkeypatch.setattr("app.services.config_service.ConfigService.get", fake_get)

    assert await resolve_prompt_assembler_flags() == (True, True)
    assert max_active == 2


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


def test_enabled_layout_renders_stable_sections_before_all_turn_context_without_marker():
    assembled = assemble_system_prompt(
        _params(
            layout_mode="enabled",
            cache_boundary_enabled=True,
            cache_reorder_enabled=True,
            user_profile="User profile",
            accessible_resources="Accessible resources",
            sub_agents_context="Sub-agent context",
            turn_decision=TurnDecision(source="current_turn"),
        )
    )

    assert "NANZI_PROMPT_CACHE_BOUNDARY" not in assembled.full_text
    assert "<!-- NANZI_CACHE_BOUNDARY -->" not in assembled.full_text
    assert assembled.full_text.index("Agent DB prompt") < assembled.full_text.index("User profile")
    assert assembled.full_text.index("Agent DB prompt") < assembled.full_text.index("本轮执行上下文")
    assert assembled.full_text.index("Agent DB prompt") < assembled.full_text.index("Accessible resources")
    assert assembled.full_text.index("Agent DB prompt") < assembled.full_text.index("Sub-agent context")
    assert assembled.section_names[:2] == ("platform_fixed", "agent_system_prompt")
    assert "platform_capabilities" in assembled.section_names
    assert "user_profile" in assembled.section_names
    assert assembled.section_char_counts["agent_system_prompt"] == len("Agent DB prompt")


def test_prompt_plan_renders_only_its_stable_then_dynamic_sections():
    plan = PromptPlan(
        stable_sections=(
            PromptSection("stable", 0, "stable text", stability="stable"),
        ),
        dynamic_sections=(
            PromptSection("dynamic", 0, "dynamic text", stability="dynamic"),
        ),
    )

    assert plan.render_stable() == "stable text"
    assert plan.render_dynamic() == "dynamic text"
    assert plan.render() == "stable text\n\ndynamic text"


def test_prompt_plan_uses_one_filtered_sorted_sequence_for_render_and_metadata():
    plan = PromptPlan(
        stable_sections=(
            PromptSection("stable_later", 20, "stable later", stability="stable"),
            PromptSection("stable_first", 10, "stable first", stability="stable"),
            PromptSection("disabled", 0, "hidden", enabled=False, stability="stable"),
        ),
        dynamic_sections=(
            PromptSection("dynamic_later", 20, "dynamic later", stability="dynamic"),
            PromptSection("dynamic_first", 10, "dynamic first", stability="dynamic"),
            PromptSection("blank", 0, "   ", stability="dynamic"),
        ),
    )

    assert plan.render_stable() == "stable first\n\nstable later"
    assert plan.render_dynamic() == "dynamic first\n\ndynamic later"
    assert plan.render() == "stable first\n\nstable later\n\ndynamic first\n\ndynamic later"
    assert plan.section_names() == (
        "stable_first",
        "stable_later",
        "dynamic_first",
        "dynamic_later",
    )
    assert plan.section_char_counts() == {
        "stable_first": len("stable first"),
        "stable_later": len("stable later"),
        "dynamic_first": len("dynamic first"),
        "dynamic_later": len("dynamic later"),
    }


def test_prompt_plan_rejects_duplicate_section_names():
    with pytest.raises(ValueError, match="重复"):
        PromptPlan(
            stable_sections=(PromptSection("same", 0, "stable", stability="stable"),),
            dynamic_sections=(PromptSection("same", 0, "dynamic", stability="dynamic"),),
        )


@pytest.mark.parametrize(
    ("runtime_tool_names", "quick_suggestions_forbidden"),
    (
        (set(), False),
        ({"Bash", "exec_command"}, False),
        ({"Bash", "ask_user_question"}, False),
        ({"browser_snapshot"}, True),
    ),
)
def test_legacy_platform_prompt_equals_fixed_and_dynamic_sections(
    runtime_tool_names, quick_suggestions_forbidden
):
    kwargs = {
        "agent_config": SimpleNamespace(tools=[]),
        "runtime_tool_names": runtime_tool_names,
        "quick_suggestions_forbidden": quick_suggestions_forbidden,
    }

    legacy = AgentServicePrompts.prepend_platform_global_system_prompt(None, **kwargs)
    split = "\n\n".join(
        (
            AgentServicePrompts.platform_fixed_system_prompt(),
            AgentServicePrompts.platform_dynamic_capability_prompt(**kwargs),
        )
    )

    assert legacy == split


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
    assert "quick 目标必须是自然语言问题" in prompt
    assert "不得把 SQL、代码或物理表名" in prompt


def test_platform_prompt_separates_current_task_from_historical_context():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=[]),
    )

    assert "最后一条用户消息决定本轮唯一可执行任务" in prompt
    assert "历史消息、上下文压缩摘要、旧工具计划和旧搜索目标" in prompt
    assert "只有当前用户明确引用或恢复历史任务时" in prompt


def test_platform_prompt_guides_generic_capability_gap_recovery():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=["Bash", "Write"]),
    )

    assert "任务能力缺口与临时方案" in prompt
    assert "优先使用当前已绑定的专用工具、Skill、MCP 和隐式工具" in prompt
    assert "检查命令、解释器和依赖" in prompt
    assert "优先使用已有依赖或标准库" in prompt
    assert "安装软件包、浏览器、命令行工具或其他运行依赖" in prompt
    assert "等待用户确认" in prompt
    assert "当前会话工作区" in prompt


def test_platform_prompt_degrades_without_execution_capability():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=[]),
    )

    assert "没有对应执行能力时，只能输出方案、代码或待执行文件" in prompt
    assert "不得声称已经完成" in prompt
    assert "不得通过提示词自行扩大工具权限" in prompt
    assert "注册正式工具或 MCP" in prompt


def test_platform_prompt_keeps_existing_sensitive_tool_confirmation():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=["Bash"]),
    )

    assert "## 任务能力缺口与临时方案" in prompt
    assert "## 工具确认" in prompt
    assert "不得声称已执行" in prompt


def test_platform_prompt_guides_todo_for_multi_step_work():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names=["todo_write"],
    )

    assert "todo_write" in prompt
    assert "多个执行步骤" in prompt
    assert "单步问答、单次检索和单次查询不要调用" in prompt


def test_platform_prompt_requires_publishing_generated_files_for_download():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names={"Write", "publish_generated_file"},
    )

    assert "publish_generated_file" in prompt
    assert "生成下载地址" in prompt
    assert "download_url" in prompt
    assert "不得返回物理路径或臆造链接" in prompt


def test_platform_prompt_describes_bound_office_read_write_tools():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names={
            "word_document_read",
            "word_document_write",
            "excel_document_read",
            "excel_document_write",
        },
    )

    assert "word_document_read" in prompt
    assert "word_document_write" in prompt
    assert "excel_document_read" in prompt
    assert "excel_document_write" in prompt
    assert "Word/Excel" in prompt
    assert "artifact.download_url" in prompt
    assert "必须优先调用对应的 *_read 工具" in prompt
    assert "必须优先调用对应的 *_write 工具" in prompt


def test_platform_prompt_does_not_claim_unbound_office_tools():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names={"word_document_write"},
    )

    assert "word_document_write" in prompt
    assert "excel_document_write" not in prompt
    assert "excel_document_read" not in prompt
    assert "Word/Excel" not in prompt
    assert "Word 文件" in prompt


def test_platform_prompt_office_write_does_not_require_duplicate_publish():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names={"word_document_write", "publish_generated_file"},
    )

    assert "word_document_write" in prompt
    assert "不要再次调用 publish_generated_file" in prompt


def test_platform_prompt_inventory_uses_effective_runtime_tool_names():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=["search_knowledge_base", "memory_search"]),
        runtime_tool_names={"memory_search"},
    )

    assert "## 本轮可用工具" in prompt
    assert "- memory_search:" in prompt
    assert "- search_knowledge_base:" not in prompt


def test_platform_prompt_inventory_keeps_configured_knowledge_tool_for_knowledge_turn():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=["search_knowledge_base"]),
        runtime_tool_names={"search_knowledge_base"},
    )

    assert "- search_knowledge_base:" in prompt


def test_session_status_prompt_explains_runtime_snapshot_trigger():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names={"session_status"},
    )

    assert "session_status" in prompt
    assert "会话、设备、模型上下文、工作区、沙箱策略和后端运行环境" in prompt
    assert "只读" in prompt
    assert "优先调用 **session_status**" in prompt


def test_session_status_prompt_guidance_is_absent_when_tool_is_unavailable():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        runtime_tool_names={"get_current_model"},
    )

    assert "会话、设备、模型上下文、工作区、沙箱策略和后端运行环境" not in prompt
    assert "优先调用 **session_status**" not in prompt


def test_effective_prompt_tool_names_uses_configured_tools_and_enabled_flag():
    config = SimpleNamespace(
        agent_name="TestAgent",
        tools=[
            "search_knowledge_base",
            {"name": "execute_sql_query", "enabled": False},
        ],
    )

    names = resolve_effective_prompt_tool_names(config)

    assert "search_knowledge_base" in names
    assert "execute_sql_query" not in names


def test_effective_prompt_tool_names_exposes_configured_tools():
    config = SimpleNamespace(
        agent_name="TestAgent",
        tools=["Bash", "excel_document_write"],
    )
    names = resolve_effective_prompt_tool_names(config)

    assert "excel_document_write" in names
    assert "Bash" in names


@pytest.mark.asyncio
async def test_effective_prompt_tool_names_filters_acquisition_tools_on_reuse():
    config = SimpleNamespace(
        agent_name="TestAgent",
        tools=["sub_agent_call", "execute_sql_query", "write_file"],
    )
    decision = TurnDecision(
        reusable_result_mode="reuse",
        reusable_result_id="result-1",
    )

    names = await resolve_effective_prompt_tool_names_for_turn(
        config,
        current_user_query="生成分析报告",
        turn_decision=decision,
    )

    assert "write_file" in names
    assert "sub_agent_call" not in names
    assert "execute_sql_query" not in names


def test_platform_prompt_prefers_mermaid_for_structural_diagrams_only():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=[]),
    )

    assert "流程图、原理图、系统架构图、组织架构图" in prompt
    assert "优先使用 Mermaid" in prompt
    assert "```mermaid" in prompt
    assert "```chart``` / ECharts" in prompt
    assert "Mermaid 仅用于流程图" in prompt


def test_platform_prompt_applies_echarts_contract_to_all_numeric_data_charts():
    prompt = AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=SimpleNamespace(tools=[]),
    )

    assert "全平台数据图表" in prompt
    assert "趋势、排名、分类、占比" in prompt
    assert "禁止使用 Mermaid、xychart" in prompt
    assert "必须使用 ```chart``` 代码块" in prompt
    assert "series 必须是数组" in prompt
    assert "禁止 JavaScript 函数" in prompt
    assert "不得使用根节点 type + data.datasets" in prompt
    assert "candlestick" in prompt
    assert "line、bar、pie、scatter、gauge、radar、funnel、heatmap、treemap、candlestick" in prompt


def test_multi_agent_synthesis_prompt_keeps_global_echarts_contract():
    prompt = AgentServicePrompts.MULTI_AGENT_SYNTHESIS_SYSTEM

    assert "全平台数据图表" in prompt
    assert "禁止使用 Mermaid、xychart" in prompt
    assert "必须使用 ```chart``` 代码块" in prompt


def test_interactive_prompt_keeps_inspirational_quick_suggestions_by_default():
    assembled = assemble_system_prompt(_params())

    assert "普通交互式会话" in assembled.full_text
    assert "尽可能提供 2-3 个" in assembled.full_text
    assert "quick_suggestions_forbidden=true" not in assembled.full_text


def test_platform_prompt_prioritizes_explicit_question_requests():
    assembled = assemble_system_prompt(_params())

    assert "用户明确要求提问" in assembled.full_text
    assert "主动互动模式" in assembled.full_text
    assert "列出问题" in assembled.full_text


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

    assert prompt.count("[NanZi智能体平台 · 全局守则]") == 1
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


def test_prompt_includes_normalized_turn_context_without_replacing_agent_prompt():
    assembled = assemble_system_prompt(
        _params(
            turn_decision=TurnDecision(
                source="internal_structured_data",
                capability="data_query",
                semantic_intent="DATA_QUERY",
                relation_to_previous="new_topic",
                reference_mode="new_query",
                freshness_requirement="realtime",
                needs_fresh_data=True,
                allows_data_route=True,
            )
        )
    )

    assert "## 本轮执行上下文（平台路由快照）" in assembled.full_text
    assert "请求来源：internal_structured_data" in assembled.full_text
    assert "路由层已允许进入结构化业务数据能力" in assembled.full_text
    assert assembled.full_text.index("本轮执行上下文") < assembled.full_text.index("Agent DB prompt")
    assert "turn_decision" in assembled.section_names
    assert assembled.section_char_counts["turn_decision"] > 0


def test_prompt_includes_accessible_resources_as_a_separate_dynamic_section():
    assembled = assemble_system_prompt(
        _params(
            user_profile="<USER_PROFILE>\n- Account Name: alice\n</USER_PROFILE>",
            accessible_resources=(
                "## 当前用户可访问的内部资源摘要\n"
                "### 知识库\n"
                "- 蔚来汽车手册：辅助驾驶和车辆使用说明"
            ),
        )
    )

    assert "蔚来汽车手册" in assembled.full_text
    assert assembled.section_names.index("user_profile") < assembled.section_names.index(
        "accessible_resources"
    )
    assert assembled.section_names.index("accessible_resources") < assembled.section_names.index(
        "agent_system_prompt"
    )
    assert assembled.section_char_counts["accessible_resources"] > 0
