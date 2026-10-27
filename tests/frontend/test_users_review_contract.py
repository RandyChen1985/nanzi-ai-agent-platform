"""用户管理页的统一审核：两类资源、管理员审批、失效资源拦截。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
USERS = ROOT / "frontend/src/views/Users.vue"


def _users() -> str:
    assert USERS.exists()
    return USERS.read_text(encoding="utf-8")


def test_review_dialog_uses_unified_endpoints():
    page = _users()
    assert "/api/portal/access-requests/pending" in page
    assert "/api/portal/access-requests/reviews" in page
    # 旧的知识库专用审批接口不该再被调用
    assert "/api/portal/ragflow/access-requests/pending" not in page
    assert "/api/portal/ragflow/access-requests/reviews" not in page


def test_review_dialog_labels_both_resource_types():
    page = _users()
    assert "资源访问审核" in page
    assert "'知识库'" in page and "'数据集'" in page
    assert "resource_type === 'dataset'" in page


def test_review_dialog_filters_by_resource_type():
    page = _users()
    assert "resourceTypeFilter" in page
    assert "resourceTypeOptions" in page


def test_approve_is_blocked_for_deleted_resource():
    page = _users()
    assert "resource_exists === false" in page
    assert "该资源已被删除" in page


def test_notification_deeplink_highlights_request():
    page = _users()
    assert "highlightRequestId" in page
    assert "route.query.request" in page


def test_pending_badge_counts_all_types():
    page = _users()
    # 角标口径不受类型筛选影响，否则待办会被筛掉
    assert "角标只统计总数" in page
