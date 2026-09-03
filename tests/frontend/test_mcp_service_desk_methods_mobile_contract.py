from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _source() -> str:
    return (ROOT / "frontend/src/views/McpServiceDesk.vue").read_text(encoding="utf-8")


def test_methods_tab_has_responsive_table_and_mobile_cards():
    source = _source()
    methods_section = source.split("activeTab === 'methods'", 1)[1].split("activeTab === 'audit'", 1)[0]

    assert "md:block" in methods_section
    assert "md:hidden" in methods_section
    assert "method.name" in methods_section
    assert "method.scope" in methods_section
    assert "method.capability_group" in methods_section
    assert "必须用户授权" in methods_section
    assert "待接入" in methods_section


def test_service_desk_tabs_stay_single_line_and_scroll_on_mobile():
    source = _source()
    tab_bar = source.split("availableTabs.length", 1)[1].split("activeTab === 'guide'", 1)[0]

    assert "overflow-x-auto" in tab_bar
    assert "flex-nowrap" in tab_bar
    assert "whitespace-nowrap" in tab_bar
    assert "shrink-0" in tab_bar
