from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Iterable, List, Optional

from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.ai.prompt_sections import PromptSection, render_prompt_sections
from app.services.ai.turn_decision import TurnDecision

NANZI_PROMPT_CACHE_BOUNDARY = "\n<!-- NANZI_CACHE_BOUNDARY -->\n"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptLayoutConfig:
    """System-prompt layout rollout settings resolved from system configuration."""

    mode: str = "legacy"
    rollout_percent: int = 0


@dataclass(frozen=True)
class PromptPlan:
    """A deterministic split between shareable and per-turn prompt sections."""

    stable_sections: tuple[PromptSection, ...] = ()
    dynamic_sections: tuple[PromptSection, ...] = ()

    def __post_init__(self) -> None:
        names: set[str] = set()
        duplicates: set[str] = set()
        for section in (*self.stable_sections, *self.dynamic_sections):
            if section.name in names:
                duplicates.add(section.name)
            names.add(section.name)
        if duplicates:
            raise ValueError(f"PromptPlan 中存在重复 section name: {', '.join(sorted(duplicates))}")

    @staticmethod
    def _filtered_sorted(sections: tuple[PromptSection, ...]) -> tuple[PromptSection, ...]:
        return tuple(
            section
            for section in sorted(sections, key=lambda section: (section.order, section.name))
            if section.enabled and section.text and section.text.strip()
        )

    @staticmethod
    def _render_ordered(sections: tuple[PromptSection, ...]) -> str:
        return "\n\n".join(section.text.strip() for section in sections)

    def render_stable(self) -> str:
        return self._render_ordered(self._filtered_sorted(self.stable_sections))

    def render_dynamic(self) -> str:
        return self._render_ordered(self._filtered_sorted(self.dynamic_sections))

    def render(self) -> str:
        return _join_blocks([self.render_stable(), self.render_dynamic()])

    def section_names(self) -> tuple[str, ...]:
        return tuple(
            section.name
            for section in (
                *self._filtered_sorted(self.stable_sections),
                *self._filtered_sorted(self.dynamic_sections),
            )
        )

    def section_char_counts(self) -> dict[str, int]:
        return {
            section.name: len(section.text.strip())
            for section in (
                *self._filtered_sorted(self.stable_sections),
                *self._filtered_sorted(self.dynamic_sections),
            )
        }


@dataclass(frozen=True)
class AssembledSystemPrompt:
    full_text: str
    stable_prefix: str
    dynamic_suffix: str
    cache_boundary_enabled: bool
    cache_reorder_enabled: bool
    section_names: tuple[str, ...] = ()
    section_char_counts: dict[str, int] | None = None


@dataclass
class PromptAssemblyInput:
    agent_system_prompt: Optional[str]
    agent_config: Any
    engine_type: str
    skills_injection: List[str]
    skills_already_loaded: bool
    skills_dir: str
    ltm_profile: Optional[str] = None
    memory_recall_hint: Optional[str] = None
    preloaded_memories: Optional[str] = None
    user_profile: Optional[str] = None
    accessible_resources: Optional[str] = None
    cache_boundary_enabled: bool = False
    cache_reorder_enabled: bool = False
    layout_mode: str = "legacy"
    prompt_layout_mode: Optional[str] = None
    sub_agents_context: Optional[str] = None
    quick_suggestions_forbidden: bool = False
    runtime_tool_names: Optional[Iterable[str]] = None
    turn_decision: Optional[TurnDecision] = None

    def __post_init__(self) -> None:
        if self.prompt_layout_mode is not None:
            self.layout_mode = self.prompt_layout_mode
        else:
            self.prompt_layout_mode = self.layout_mode


def resolve_effective_prompt_tool_names(
    agent_config: Any,
    *,
    current_user_query: str | None = None,
    turn_decision: TurnDecision | None = None,
) -> set[str]:
    """Build the tool inventory shown to the model for the current turn.

    The published tool configuration is the source of truth. Disabled tools
    are excluded so the model cannot call a name that AgentScope did not
    register. When the production current-turn boundary is available, apply
    the same gate used by the runtime so prompt inventory and executable tools
    cannot drift apart.
    """
    names: set[str] = set()
    for item in getattr(agent_config, "tools", None) or []:
        if isinstance(item, Mapping):
            if item.get("enabled", True) is False:
                continue
            name = item.get("name")
        elif isinstance(item, str):
            name = item
        else:
            if getattr(item, "enabled", True) is False:
                continue
            name = getattr(item, "name", "")
        normalized = str(name or "").strip()
        if normalized:
            names.add(normalized)

    try:
        from app.services.ai.tools.registry import ToolRegistry

        names.update(
            str(getattr(tool, "name", "") or "").strip()
            for tool in ToolRegistry.get_system_implicit_tools()
            if str(getattr(tool, "name", "") or "").strip()
        )
    except Exception:
        pass

    try:
        from app.services.ai.skill_resolver import is_main_general_agent

        if is_main_general_agent(agent_config):
            names.add("sub_agent_call")
            names.add("sub_agent_batch_call")
            names.add("todo_write")
    except Exception:
        pass

    return names


async def resolve_effective_prompt_tool_names_for_turn(
    agent_config: Any,
    *,
    current_user_query: str,
    turn_decision: TurnDecision,
) -> set[str]:
    """Resolve prompt names for the turn directly from configured and implicit tools."""
    names = resolve_effective_prompt_tool_names(agent_config)
    if str(getattr(turn_decision, "reusable_result_mode", "none") or "none").strip().lower() == "reuse":
        from app.services.ai.session_tool_artifact import REUSABLE_RESULT_ACQUISITION_TOOLS

        names.difference_update(REUSABLE_RESULT_ACQUISITION_TOOLS)
    return names


def _prepend_block(current: str, block: Optional[str]) -> str:
    trimmed = (block or "").strip()
    if not trimmed:
        return current
    base = (current or "").strip()
    if base:
        return f"{trimmed}\n\n{base}"
    return trimmed


def _join_blocks(blocks: List[str]) -> str:
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def _skills_or_discovery_block(
    *,
    skills_injection: List[str],
    skills_already_loaded: bool,
    skills_dir: str,
) -> str:
    if skills_injection:
        return AgentServicePrompts.skills_profile(skills_injection)
    if not skills_already_loaded:
        return AgentServicePrompts.skill_discovery_hint(skills_dir)
    return ""


async def resolve_prompt_assembler_flags() -> tuple[bool, bool]:
    import asyncio

    from app.services.config_service import ConfigService

    boundary_raw, reorder_raw = await asyncio.gather(
        ConfigService.get("agent_prompt_cache_boundary_enabled", "false"),
        ConfigService.get("agent_prompt_cache_reorder_enabled", "false"),
    )

    def _enabled(raw: Optional[str]) -> bool:
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    return _enabled(boundary_raw), _enabled(reorder_raw)


def _normalize_prompt_layout_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"legacy", "observe", "enabled"} else "legacy"


def _normalize_prompt_cache_rollout_percent(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        percent = value
    else:
        raw = str(value or "").strip()
        if not raw.isascii() or not raw.isdecimal():
            return 0
        percent = int(raw)
    return percent if 0 <= percent <= 100 else 0


async def resolve_prompt_layout_config() -> PromptLayoutConfig:
    """Read the explicit layout rollout settings without altering legacy flags.

    An invalid or missing value fails closed to the legacy layout.  In
    particular, an explicit ``legacy`` value is never overridden by the older
    cache-boundary/cache-reorder booleans.
    """
    import asyncio

    from app.services.config_service import ConfigService

    mode_raw, rollout_raw = await asyncio.gather(
        ConfigService.get("agent_prompt_layout_mode", "legacy"),
        ConfigService.get("agent_prompt_cache_rollout_percent", "0"),
    )
    return PromptLayoutConfig(
        mode=_normalize_prompt_layout_mode(mode_raw),
        rollout_percent=_normalize_prompt_cache_rollout_percent(rollout_raw),
    )


def should_use_prompt_cache_layout(
    mode: str,
    rollout_percent: int | str,
    conversation_id: str | None,
) -> bool:
    """Choose the enabled layout deterministically for one conversation.

    Empty conversation identifiers deliberately stay on the legacy layout so
    callers cannot accidentally make a non-sticky rollout decision.
    """
    if _normalize_prompt_layout_mode(mode) != "enabled":
        return False
    percent = _normalize_prompt_cache_rollout_percent(rollout_percent)
    conversation_key = str(conversation_id or "").strip()
    if not conversation_key or percent <= 0:
        return False
    if percent >= 100:
        return True
    bucket = int.from_bytes(
        hashlib.sha256(conversation_key.encode("utf-8")).digest()[:8],
        byteorder="big",
    ) % 100
    return bucket < percent


def _build_stack_without_platform(params: PromptAssemblyInput) -> str:
    """Mirror AgentService prepend order: skills -> ltm -> recall -> preloaded -> user_profile."""
    prompt = (params.agent_system_prompt or "").strip()
    skills_block = _skills_or_discovery_block(
        skills_injection=params.skills_injection,
        skills_already_loaded=params.skills_already_loaded,
        skills_dir=params.skills_dir,
    )
    prompt = _prepend_block(prompt, skills_block)
    prompt = _prepend_block(prompt, params.ltm_profile)
    prompt = _prepend_block(prompt, params.memory_recall_hint)
    prompt = _prepend_block(prompt, params.preloaded_memories)
    prompt = _prepend_block(prompt, params.accessible_resources)
    prompt = _prepend_block(prompt, params.user_profile)
    prompt = _prepend_block(
        prompt,
        AgentServicePrompts.turn_decision_context(params.turn_decision),
    )
    return prompt


def _platform_global_only(params: PromptAssemblyInput) -> str:
    if (params.engine_type or "LOCAL") != "LOCAL":
        return ""
    return AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=params.agent_config,
        quick_suggestions_forbidden=params.quick_suggestions_forbidden,
        runtime_tool_names=params.runtime_tool_names,
    ).strip()


def _enabled_prompt_plan(params: PromptAssemblyInput) -> PromptPlan:
    """Build the only enabled-layout rendering plan.

    Stable sections intentionally contain no user, resource, memory, skill or
    per-turn routing content.  Tool capability text is dynamic because the
    runtime registration is the authority for this specific request.
    """
    platform_fixed = ""
    platform_capabilities = ""
    if (params.engine_type or "LOCAL") == "LOCAL":
        platform_fixed = AgentServicePrompts.platform_fixed_system_prompt()
        platform_capabilities = AgentServicePrompts.platform_dynamic_capability_prompt(
            agent_config=params.agent_config,
            quick_suggestions_forbidden=params.quick_suggestions_forbidden,
            runtime_tool_names=params.runtime_tool_names,
        )

    skills_block = _skills_or_discovery_block(
        skills_injection=params.skills_injection,
        skills_already_loaded=params.skills_already_loaded,
        skills_dir=params.skills_dir,
    )
    return PromptPlan(
        stable_sections=(
            PromptSection("platform_fixed", 0, platform_fixed, stability="stable", source="platform"),
            PromptSection(
                "agent_system_prompt",
                10,
                params.agent_system_prompt or "",
                stability="stable",
                source="agent",
            ),
        ),
        dynamic_sections=(
            PromptSection(
                "platform_capabilities",
                0,
                platform_capabilities,
                stability="dynamic",
                source="runtime_tools",
            ),
            PromptSection(
                "turn_decision",
                10,
                AgentServicePrompts.turn_decision_context(params.turn_decision),
                stability="dynamic",
                source="router",
            ),
            PromptSection("user_profile", 20, params.user_profile or "", source="user_context"),
            PromptSection(
                "accessible_resources",
                30,
                params.accessible_resources or "",
                source="resource_catalog",
            ),
            PromptSection("preloaded_memories", 40, params.preloaded_memories or "", source="memory"),
            PromptSection("memory_recall", 50, params.memory_recall_hint or "", source="memory"),
            PromptSection("ltm_profile", 60, params.ltm_profile or "", source="memory"),
            PromptSection("skills", 70, skills_block, source="skill"),
            PromptSection("sub_agents_context", 80, params.sub_agents_context or "", source="sub_agents"),
        ),
    )


def assemble_system_prompt(params: PromptAssemblyInput) -> AssembledSystemPrompt:
    if _normalize_prompt_layout_mode(params.layout_mode) == "enabled":
        plan = _enabled_prompt_plan(params)
        return AssembledSystemPrompt(
            full_text=plan.render(),
            stable_prefix=plan.render_stable(),
            dynamic_suffix=plan.render_dynamic(),
            cache_boundary_enabled=params.cache_boundary_enabled,
            cache_reorder_enabled=True,
            section_names=plan.section_names(),
            section_char_counts=plan.section_char_counts(),
        )

    stack_without_platform = _build_stack_without_platform(params)
    platform_global = _platform_global_only(params)
    if params.sub_agents_context:
        platform_global = _join_blocks([platform_global, params.sub_agents_context])
    agent_db = (params.agent_system_prompt or "").strip()

    dynamic_sections = [
        PromptSection(
            name=name,
            order=index,
            text=text,
            stability="dynamic",
            source="runtime",
        )
        for index, (name, text) in enumerate(
            (
                ("turn_decision", AgentServicePrompts.turn_decision_context(params.turn_decision)),
                ("accessible_resources", params.accessible_resources),
                ("preloaded_memories", params.preloaded_memories),
                ("memory_recall", params.memory_recall_hint),
                ("ltm_profile", params.ltm_profile),
                (
                    "skills",
                    _skills_or_discovery_block(
                        skills_injection=params.skills_injection,
                        skills_already_loaded=params.skills_already_loaded,
                        skills_dir=params.skills_dir,
                    ),
                ),
            )
        )
        if text and text.strip()
    ]
    dynamic_suffix = render_prompt_sections(dynamic_sections)

    section_blocks = [
        PromptSection("platform_global", 0, platform_global, stability="stable", source="platform"),
        PromptSection(
            "turn_decision",
            10,
            AgentServicePrompts.turn_decision_context(params.turn_decision),
            source="router",
        ),
        PromptSection("user_profile", 20, params.user_profile, source="user_context"),
        PromptSection(
            "accessible_resources",
            25,
            params.accessible_resources,
            source="resource_catalog",
        ),
        PromptSection("preloaded_memories", 30, params.preloaded_memories, source="memory"),
        PromptSection("memory_recall", 40, params.memory_recall_hint, source="memory"),
        PromptSection("ltm_profile", 50, params.ltm_profile, source="memory"),
        PromptSection(
            "skills",
            60,
            _skills_or_discovery_block(
                skills_injection=params.skills_injection,
                skills_already_loaded=params.skills_already_loaded,
                skills_dir=params.skills_dir,
            ),
            source="skill",
        ),
        PromptSection("agent_system_prompt", 70, params.agent_system_prompt, stability="stable", source="agent"),
    ]
    section_names = tuple(
        section.name
        for section in sorted(section_blocks, key=lambda item: (item.order, item.name))
        if section.enabled and section.text and section.text.strip()
    )
    section_char_counts = {
        section.name: len(section.text.strip())
        for section in section_blocks
        if section.enabled and section.text and section.text.strip()
    }

    if params.cache_reorder_enabled:
        stable_prefix = _join_blocks([part for part in [platform_global, params.user_profile, agent_db] if part])
        if params.cache_boundary_enabled and dynamic_suffix:
            full_text = f"{stable_prefix}{NANZI_PROMPT_CACHE_BOUNDARY}{dynamic_suffix}"
        elif params.cache_boundary_enabled:
            full_text = stable_prefix
        else:
            full_text = _join_blocks([stable_prefix, dynamic_suffix]) if dynamic_suffix else stable_prefix
        return AssembledSystemPrompt(
            full_text=full_text,
            stable_prefix=stable_prefix,
            dynamic_suffix=dynamic_suffix,
            cache_boundary_enabled=params.cache_boundary_enabled,
            cache_reorder_enabled=True,
            section_names=section_names,
            section_char_counts=section_char_counts,
        )

    if (params.engine_type or "LOCAL") == "LOCAL":
        if params.cache_boundary_enabled and platform_global and stack_without_platform:
            full_text = f"{platform_global}{NANZI_PROMPT_CACHE_BOUNDARY}{stack_without_platform}"
        else:
            full_text = AgentServicePrompts.prepend_platform_global_system_prompt(
                stack_without_platform or None,
                agent_config=params.agent_config,
                quick_suggestions_forbidden=params.quick_suggestions_forbidden,
                runtime_tool_names=params.runtime_tool_names,
            )
    else:
        full_text = stack_without_platform

    stable_prefix = _join_blocks([part for part in [platform_global, params.user_profile, agent_db] if part])
    return AssembledSystemPrompt(
        full_text=full_text,
        stable_prefix=stable_prefix,
        dynamic_suffix=dynamic_suffix,
        cache_boundary_enabled=params.cache_boundary_enabled,
        cache_reorder_enabled=False,
        section_names=section_names,
        section_char_counts=section_char_counts,
    )
