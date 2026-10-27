"""站内消息：资源访问申请与结果的跳转分支。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
BELL = ROOT / "frontend/src/components/PortalNotificationBell.vue"


def _bell() -> str:
    assert BELL.exists()
    return BELL.read_text(encoding="utf-8")


def test_new_categories_route_to_approval_dialog():
    bell = _bell()
    assert 'category === "resource_access"' in bell
    assert 'query: { review: "1", request: String(meta.request_id || "") }' in bell


def test_result_category_routes_to_my_requests():
    bell = _bell()
    assert 'category === "resource_access_result"' in bell
    assert 'subtab: "requests"' in bell


def test_legacy_categories_still_handled():
    bell = _bell()
    # 历史消息仍在库里，分支不能直接删
    assert 'category === "knowledge_access"' in bell
    assert 'category === "knowledge_access_result"' in bell


def test_labels_cover_both_generations():
    bell = _bell()
    assert '"资源申请"' in bell
    assert '"申请结果"' in bell
