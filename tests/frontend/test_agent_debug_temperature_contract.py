"""调试页温度必须能显式下发，包括「强制 0」。

原实现用 `if (debugConfig.temperature > 0)` 判断是否下发，
于是滑块拖到 0 会被当成「没设置」，静默回退到智能体版本温度，
调试页看起来调了 0 实际发出的却是版本温度。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_debug_view_treats_zero_temperature_as_an_explicit_override():
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert "debugConfig.temperature > 0" not in debug
    assert "temperatureOverride" in debug
    assert "debugConfig.temperatureOverride !== null" in debug


def test_debug_view_resets_and_initializes_the_temperature_override():
    debug = _read("frontend/src/views/AgentDebug.vue")

    assert "temperatureOverride: null" in debug
    assert "debugConfig.temperatureOverride = null" in debug


def test_debug_panel_slider_writes_through_the_explicit_override():
    panel = _read("frontend/src/components/DebugConfigPanel.vue")

    assert "temperatureOverride" in panel
    assert "温度 (Temperature)" in panel
