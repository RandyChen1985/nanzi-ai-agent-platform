from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from app.services.ai.agent_prompts import AgentServicePrompts

NANZI_PROMPT_CACHE_BOUNDARY = "\n<!-- NANZI_CACHE_BOUNDARY -->\n"


@dataclass(frozen=True)
class AssembledSystemPrompt:
    full_text: str
    stable_prefix: str
    dynamic_suffix: str
    cache_boundary_enabled: bool
    cache_reorder_enabled: bool


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
    cache_boundary_enabled: bool = False
    cache_reorder_enabled: bool = False
    sub_agents_context: Optional[str] = None


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
    from app.services.config_service import ConfigService

    boundary_raw = await ConfigService.get("agent_prompt_cache_boundary_enabled", "false")
    reorder_raw = await ConfigService.get("agent_prompt_cache_reorder_enabled", "false")

    def _enabled(raw: Optional[str]) -> bool:
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    return _enabled(boundary_raw), _enabled(reorder_raw)


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
    prompt = _prepend_block(prompt, params.user_profile)
    return prompt


def _platform_global_only(params: PromptAssemblyInput) -> str:
    if (params.engine_type or "LOCAL") != "LOCAL":
        return ""
    return AgentServicePrompts.prepend_platform_global_system_prompt(
        None,
        agent_config=params.agent_config,
    ).strip()


def assemble_system_prompt(params: PromptAssemblyInput) -> AssembledSystemPrompt:
    stack_without_platform = _build_stack_without_platform(params)
    platform_global = _platform_global_only(params)
    if params.sub_agents_context:
        platform_global = _join_blocks([platform_global, params.sub_agents_context])
    agent_db = (params.agent_system_prompt or "").strip()

    dynamic_blocks = [
        block
        for block in [
            params.preloaded_memories,
            params.memory_recall_hint,
            params.ltm_profile,
            _skills_or_discovery_block(
                skills_injection=params.skills_injection,
                skills_already_loaded=params.skills_already_loaded,
                skills_dir=params.skills_dir,
            ),
        ]
        if block and block.strip()
    ]
    dynamic_suffix = _join_blocks(dynamic_blocks)

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
        )

    if (params.engine_type or "LOCAL") == "LOCAL":
        if params.cache_boundary_enabled and platform_global and stack_without_platform:
            full_text = f"{platform_global}{NANZI_PROMPT_CACHE_BOUNDARY}{stack_without_platform}"
        else:
            full_text = AgentServicePrompts.prepend_platform_global_system_prompt(
                stack_without_platform or None,
                agent_config=params.agent_config,
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
    )
