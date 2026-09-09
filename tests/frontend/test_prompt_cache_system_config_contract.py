from pathlib import Path
import pytest

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"
SYSTEM_CONFIG = FRONTEND_ROOT / "src" / "views" / "SystemConfig.vue"


def test_prompt_cache_settings_precede_agent_iteration_limit():
    """SystemConfig 中 agent_prompt_layout_mode 与 agent_prompt_cache_rollout_percent 必须排在 agent_max_iterations 之前。"""
    assert SYSTEM_CONFIG.exists(), f"{SYSTEM_CONFIG} 不存在"
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    category_idx = source.index("category === 'agent'")
    order_idx = source.index("const order = [", category_idx)
    order_block = source[order_idx : source.index("]", order_idx)]

    assert "agent_prompt_layout_mode" in order_block
    assert "agent_prompt_cache_rollout_percent" in order_block
    assert "agent_max_iterations" in order_block

    assert order_block.index("agent_prompt_layout_mode") < order_block.index("agent_max_iterations")
    assert order_block.index("agent_prompt_cache_rollout_percent") < order_block.index("agent_max_iterations")


def test_prompt_cache_settings_have_specialized_controls_and_tips():
    """SystemConfig 必须具备专用的下拉和滑块控件，并包含对应提示文案。"""
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    assert "agent_prompt_layout_mode" in source
    assert "agent_prompt_cache_rollout_percent" in source
    assert "legacy (传统布局" in source
    assert "observe (仅观测" in source
    assert "enabled (启用优化" in source
