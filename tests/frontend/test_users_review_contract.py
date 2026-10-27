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


def test_review_entry_is_admin_only():
    """审核入口与整套接口都是管理员专属：缺权限时不该出现，也不该白打接口。

    后端 pending / reviews / approve 全是 require_admin，前端若照样渲染按钮，
    非管理员点进去只会收到一条 Admin access required —— 入口本身就是误导。
    """
    page = _users()
    block = page[page.index("<!-- 审核入口"):]
    block = block[: block.index("</button>")]
    assert 'v-if="isAdmin"' in block
    # 角标计数也只在管理员时拉，避免一进用户管理页就吃一个 403
    assert "if (isAdmin.value) {" in page
    # 深链 ?review=1 与其它调用方统一走守卫
    assert "if (!isAdmin.value) return;" in page


def test_quota_entry_requires_user_edit_permission():
    """没有「用户编辑」权限时，「更多」与「系统默认额度」入口都该消失。"""
    page = _users()
    idx = page.index("系统默认额度")
    # 按钮开标签在文案之前，取前面窗口断言它的显示条件
    assert 'v-if="canEditUser"' in page[max(0, idx - 400): idx]
    # 「更多」面板整体只看编辑权限：否则没有权限的人会看到一个点开全空的菜单
    assert "const showMoreMenuPanel = computed(() => canEditUser.value)" in page
    # 旧的菜单权限判定不得回退（它会让只有菜单权限的人看到额度入口）
    assert "canOpenSystemQuota" not in page
