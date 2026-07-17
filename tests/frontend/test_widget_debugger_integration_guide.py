from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.no_infrastructure


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_widget_debugger_guide_uses_runtime_origin_and_current_api_key():
    source = _source("frontend/src/views/WidgetDebugger.vue")

    assert "window.location.origin" in source
    assert "integrationHost" in source
    assert "integrationApiKey" in source
    assert "YOUR_TOKEN" not in source
    assert "https://your-yunshu-domain" not in source


def test_widget_debugger_guide_supports_auto_and_explicit_agent_modes():
    source = _source("frontend/src/views/WidgetDebugger.vue")

    assert "integrationAgentMode" in source
    assert "selectedIntegrationAgentId" in source
    assert "/api/portal/agents/allowed" in source
    assert "buildAgentQueryParam" in source
    assert "agent_id: buildAgentInitValue()" in source


def test_widget_debugger_copy_guide_shows_toast_feedback():
    source = _source("frontend/src/views/WidgetDebugger.vue")

    assert "useToast" in source
    assert "const { showToast } = useToast()" in source
    assert "showToast('集成代码已复制到剪贴板', 'success')" in source
    assert "showToast('复制失败，请手动复制代码', 'error')" in source


def test_widget_debugger_floating_guide_can_collapse_after_opening():
    source = _source("frontend/src/views/WidgetDebugger.vue")

    assert "nanzi-widget-collapse" in source
    assert "shell.classList.add('collapsed')" in source
    assert "收起助手" in source
