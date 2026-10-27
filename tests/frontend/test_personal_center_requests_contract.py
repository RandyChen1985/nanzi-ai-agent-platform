"""个人中心「我的申请」：两类资源统一展示，已通过可进只读概览。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/PersonalCenter.vue"


def _page() -> str:
    assert PAGE.exists()
    return PAGE.read_text(encoding="utf-8")


def test_uses_unified_requests_endpoint():
    page = _page()
    assert "/api/portal/access-requests/mine" in page
    assert "/api/portal/access-requests/${item.id}/cancel" in page
    assert "/api/portal/ragflow/access-requests" not in page


def test_items_show_resource_type_badge():
    page = _page()
    assert "requestResourceLabel" in page
    assert "'知识库'" in page and "'数据集'" in page


def test_empty_state_points_to_resource_square():
    page = _page()
    assert "资源广场" in page
    assert "发现" not in page.split("还没有提交过申请")[1].split("</div>")[0]


def test_approved_request_links_to_the_granted_view():
    """已授权条目要能点回去。

    知识库不再跳只读概览（当前点开是空白），改跳资源广场的「已授权」筛选；
    数据集仍走概览页。
    """
    page = _page()
    assert "openGrantedResource" in page
    assert "section: 'knowledge', status: 'has'" in page
    assert "ResourceSquareDataset" in page
    # 知识库概览页不再作为跳转目标
    assert "name: 'ResourceSquareKnowledge'" not in page
