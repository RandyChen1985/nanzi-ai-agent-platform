from types import SimpleNamespace

import pytest

from app.services.ai.prompt_assembler import (
    NANZI_PROMPT_CACHE_BOUNDARY,
    PromptAssemblyInput,
    assemble_system_prompt,
)

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
