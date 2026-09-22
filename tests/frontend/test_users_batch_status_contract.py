"""用户管理「批量启用 / 禁用」的前后端契约测试。

这类测试不依赖数据库与运行时，只锁定关键实现片段，防止后续改动
不小心把批量能力、防误操作约束或"禁用即无法登录"的提示语删掉。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

VIEW_PATH = "frontend/src/views/Users.vue"
BACKEND_PATH = "app/api/portal/endpoints/management.py"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_users_view_exposes_batch_selection_ui():
    view = _source(VIEW_PATH)

    # 多选状态与全选 / 半选
    assert "selectedUserIds" in view
    assert "isAllPageSelected" in view
    assert "isPagePartiallySelected" in view
    assert ':indeterminate.prop="isPagePartiallySelected"' in view
    assert "toggleSelectAllPage" in view
    assert "toggleSelectUser" in view

    # 批量操作条与按钮
    assert "批量启用" in view
    assert "批量禁用" in view
    assert "取消选择" in view
    assert "openBatchStatusDialog" in view
    assert "clearSelection" in view


def test_users_view_calls_batch_status_endpoint():
    view = _source(VIEW_PATH)

    assert "/api/portal/management/users/batch-status" in view
    assert "submitBatchStatus" in view
    assert "user_ids" in view
    assert "batchSubmitting" in view


def test_users_view_confirms_destructive_batch_disable():
    view = _source(VIEW_PATH)

    # 禁用是不可逆的高影响操作，必须二次确认并说明登录影响
    assert "showBatchStatusDialog" in view
    assert "batchStatusTarget" in view
    assert "确认批量禁用" in view
    assert "确认批量启用" in view
    assert "无法登录系统" in view
    assert "已登录的会话会被立即踢出" in view


def test_users_view_reports_batch_outcome_details():
    view = _source(VIEW_PATH)

    # 后端会回报跳过自己与不存在的用户，前端必须如实提示
    assert "skipped_self" in view
    assert "not_found" in view
    assert "已跳过当前登录账号" in view


def test_users_view_enforces_batch_limit_and_clears_selection():
    view = _source(VIEW_PATH)

    # 单次上限与"列表变化即清空"约束
    assert "BATCH_STATUS_MAX_USERS = 200" in view
    assert "单次最多操作" in view

    # clearSelection 必须出现在 fetchUsers 内，避免跨页残留造成误操作
    fetch_body = view.split("const fetchUsers = async () => {", 1)[1].split(
        "const fetchBusinessRoles", 1
    )[0]
    assert "clearSelection()" in fetch_body


def test_backend_exposes_batch_status_endpoint():
    backend = _source(BACKEND_PATH)

    assert "class BatchUpdateStatusRequest" in backend
    assert 'BATCH_STATUS_MAX_USERS = 200' in backend
    assert '"/users/batch-status"' in backend

    handler = backend.split("async def batch_update_user_status", 1)[1]
    assert "element:user:edit" in handler
    assert "skipped_self" in handler
    assert "not_found" in handler
    # 禁用 / 启用后必须清缓存，保证被禁用者立即掉线
    assert "invalidate_user_auth_cache" in handler
    assert "invalidate_cached_permissions_for_users" in handler
