"""资源广场 review 修复的前端契约（批次 3）。

这些用例锁住的是「用户能观察到的行为」而不是实现细节：
筛选条件必须真的生效、失败原因必须说人话、提交成功必须有反馈、
已加载的列表不能被一次失败抹掉、只读页不能被路由守卫挡住。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "frontend/src/api/resourceSquare.ts"
PANEL = ROOT / "frontend/src/components/resource-square/ResourceSquarePanel.vue"
MODAL = ROOT / "frontend/src/components/resource-square/ApplyAccessModal.vue"
DATASET_VIEW = ROOT / "frontend/src/views/ResourceSquareDataset.vue"
KNOWLEDGE_VIEW = ROOT / "frontend/src/views/ResourceSquareKnowledge.vue"
ROUTER = ROOT / "frontend/src/router/index.ts"
WORKBENCH = ROOT / "frontend/src/views/PersonalWorkbench.vue"
USERS = ROOT / "frontend/src/views/Users.vue"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_error_message_survives_being_wrapped():
    """`applyResourceAccess` 已经 throw new Error(detail) 了，取文案时要认 Error.message。

    否则弹窗读 err.response.data.detail 永远读不到，后端「你已经提交过申请，
    正在等待审批」这类文案全部变成「提交申请失败」。
    """
    src = _read(API)
    assert "error instanceof Error" in src
    assert "error.message" in src


def test_wrapped_errors_keep_http_status():
    """只包文案不包状态码，403 会退化成普通错误提示、丢掉「去申请权限」的引导。"""
    src = _read(API)
    assert "function toUserError" in src
    assert "throw new Error(messageOf(" not in src
    assert "carried" in src


def test_readonly_fetches_wrap_errors_with_backend_detail():
    src = _read(API)
    start = src.index("export async function fetchKnowledgeReadonly")
    tail = src[start:]
    assert "catch (error)" in tail
    assert "toUserError(error" in tail


def test_panel_discards_stale_responses():
    """筛选/搜索变化时旧请求的响应不能落盘（否则列表与条件不一致）。"""
    src = _read(PANEL)
    assert "kbRequestSeq" in src and "dsRequestSeq" in src
    # 响应回来先比对序号，不一致直接丢弃
    assert "seq !== " in src


def test_panel_does_not_drop_new_requests_while_loading():
    """旧实现 `if (loading) return` 会把新条件的请求直接扔掉。"""
    src = _read(PANEL)
    assert "if (append ? kbLoadingMore.value : kbLoading.value) return" not in src


def test_panel_debounces_filter_changes():
    src = _read(PANEL)
    assert "scheduleReload" in src
    assert "setTimeout" in src


def test_panel_reports_submit_success():
    """申请成功后必须给一句确认，否则弹窗一关、卡片消失像失败。"""
    src = _read(PANEL)
    assert "submitNotice" in src
    assert "已提交" in src


def test_append_failure_keeps_loaded_items():
    """加载更多失败时，已经看到的条目不能被错误块顶掉。"""
    src = _read(PANEL)
    assert 'v-if="kbError"' in src
    # 列表与错误块不再是互斥分支
    assert 'v-else-if="kbItems.length"' not in src
    assert 'v-if="kbItems.length"' in src


def test_router_exempts_readonly_square_pages():
    """只读概览就是给「没有管理菜单」的账号用的，守卫必须放行。"""
    src = _read(ROUTER)
    assert "isReadonlySquarePage" in src
    assert "ResourceSquareDataset" in src.split("isReadonlySquarePage")[1][:400]
    assert "ResourceSquareKnowledge" in src.split("isReadonlySquarePage")[1][:400]


def test_switching_tab_clears_status_filter():
    src = _read(WORKBENCH)
    start = src.index("function switchTab")
    branch = src[start : start + 600]
    assert "delete query.status" in branch


def test_dataset_tab_hides_visibility_filter():
    """数据集没有可见范围概念，留着下拉会让用户以为筛选坏了。"""
    src = _read(PANEL)
    idx = src.index('v-model="visibility"')
    assert 'v-if="showKb"' in src[max(0, idx - 200) : idx]


def test_reviewed_tab_can_page_forward():
    src = _read(USERS)
    assert "reviewedPage" in src
    assert "fetchReviewedRequests" in src

