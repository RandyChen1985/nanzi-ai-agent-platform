"""访问申请与审批的前端契约。

未授权用户此前既看不到公共库、也没有申请渠道，只能线下要权限，全程无留痕。
三个落点缺一不可：发现（申请）→ 我的申请（查进度）→ 访问审批（归属人处理）。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
PERSONAL = ROOT / "frontend/src/views/PersonalCenter.vue"
USERS = ROOT / "frontend/src/views/Users.vue"


@pytest.fixture(scope="module")
def knowledge() -> str:
    return KNOWLEDGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def personal() -> str:
    return PERSONAL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def users() -> str:
    return USERS.read_text(encoding="utf-8")


def test_discover_drawer_lists_applicable_datasets(knowledge: str) -> None:
    assert "openDiscover" in knowledge
    assert "/datasets/discover" in knowledge
    assert "申请访问" in knowledge


def test_discover_explains_private_datasets_are_excluded(knowledge: str) -> None:
    assert "个人私有库" in knowledge


def test_apply_dialog_has_reason_field(knowledge: str) -> None:
    assert "submitAccessRequest" in knowledge
    assert "access-requests" in knowledge
    assert "applyReason" in knowledge


def test_personal_center_has_my_requests_tab(personal: str) -> None:
    assert "'requests'" in personal
    assert "我的申请" in personal
    assert "/access-requests/mine" in personal


def test_my_requests_supports_cancel(personal: str) -> None:
    assert "cancelMyRequest" in personal
    assert "/cancel" in personal


def test_users_page_reviews_with_approve_and_reject(users: str) -> None:
    assert "openAccessReviews" in users
    assert "/access-requests/pending" in users
    assert "decideAccessReview" in users
    assert 'approve ? "approve" : "reject"' in users


def test_users_page_shows_pending_badge(users: str) -> None:
    assert "pendingReviewCount" in users


def test_review_records_are_reachable_after_decision(users: str) -> None:
    """审批完的记录不能凭空消失：待审批之外要有「已处理」。"""
    assert "access-requests/reviews" in users
    assert "accessReviewTab" in users
    assert "已处理" in users
    assert "还没有审批记录" in users


def test_admin_does_not_see_discover_entry(knowledge: str) -> None:
    """管理员对所有库都有权限，「发现」对他来说永远为空，不该占位。"""
    # 搜索行入口
    assert 'v-if="!bulkSelectMode && !isAdmin"' in knowledge
    # 空状态入口
    assert knowledge.count('v-if="!isAdmin"') >= 1


def test_discover_card_shows_scale_and_freshness(knowledge: str) -> None:
    """只有库名和描述没法定「值不值得申请」。"""
    assert "item.doc_count" in knowledge
    assert "item.chunk_count" in knowledge
    assert "discoverUpdatedText" in knowledge


def test_discover_card_does_not_fake_zero_counts(knowledge: str) -> None:
    """引擎读不出来时计数显示「—」，不能假装是 0 个文档。"""
    assert "item.doc_count === null" in knowledge


def test_review_card_shows_dataset_context(users: str) -> None:
    """审批人要和申请人看到同样的库画像，否则只能靠库名猜。"""
    assert "item.doc_count" in users
    assert "item.chunk_count" in users
    assert "item.visibility" in users


def test_review_card_flags_missing_dataset(users: str) -> None:
    """库在引擎侧已经没了，批准也没用，得先标出来。"""
    assert "引擎侧已失联" in users
    assert 'item.dataset_exists === false' in users
