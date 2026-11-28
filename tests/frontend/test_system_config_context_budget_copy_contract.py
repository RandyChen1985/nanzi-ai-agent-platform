"""agent_context_max_tokens 的模型上下文优先级说明契约。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure
ROOT = Path(__file__).resolve().parents[2]
SYSTEM_CONFIG = ROOT / "frontend/src/views/SystemConfig.vue"


def test_context_budget_field_explains_model_context_is_preferred_and_config_is_fallback():
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    field = source[source.index("item.key === 'agent_context_max_tokens'") :]
    field = field[: field.index("item.key === 'agent_max_context_messages'")]

    assert "系统优先采用当前模型配置的上下文窗口；本配置仅在模型未配置有效上下文大小时作为兜底预算。" in field


def test_context_budget_presets_cover_long_context_models():
    """常用预设要补到 512k / 1m：长上下文模型（256K~1M 窗口）做兜底预算时不用手敲数字。"""
    source = SYSTEM_CONFIG.read_text(encoding="utf-8")

    field = source[source.index("item.key === 'agent_context_max_tokens'") :]
    field = field[: field.index("item.key === 'agent_max_context_messages'")]

    for label, value in (
        ("16k", "16384"),
        ("64k (推荐)", "65536"),
        ("256k", "262144"),
        ("512k", "524288"),
        ("1m", "1048576"),
    ):
        assert f"{{ label: '{label}', value: '{value}' }}," in field
