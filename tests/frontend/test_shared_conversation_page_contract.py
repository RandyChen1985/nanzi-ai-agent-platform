"""分享页契约：四态齐备、先取元信息再决定要不要密码、令牌不进 URL。"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure


def _page() -> str:
    return (ROOT / "frontend/src/views/SharedConversation.vue").read_text(encoding="utf-8")


def test_page_has_four_states():
    source = _page()
    for marker in ("phase === 'loading'", "phase === 'locked'", "phase === 'ready'", "phase === 'error'"):
        assert marker in source, marker


def test_token_is_kept_in_session_storage_not_url():
    source = _page()
    assert "sessionStorage" in source
    assert "share_token:" in source
    assert "?token=" not in source


def test_page_loads_renderer_lazily_and_readonly():
    source = _page()
    assert "defineAsyncComponent" in source
    assert "readonly" in source


def test_footer_states_readonly_snapshot():
    assert "只读快照" in _page()


def test_router_registers_public_share_route():
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")
    assert "path: '/share/:shareId'" in router
    assert "public: true" in router
