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
