"""申请与审批必须能主动通知到人。

只放一个红点在用户页里，等于要求审批人每天主动去翻一遍——现实中没人这么做，
申请会一直挂着。所以三件事要成立：申请人提交后通知归属人、审批后通知申请人、
点消息能落到能动手的页面。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
BELL = ROOT / "frontend/src/components/PortalNotificationBell.vue"
USERS = ROOT / "frontend/src/views/Users.vue"
ENDPOINT = ROOT / "app/api/portal/endpoints/ragflow.py"


@pytest.fixture(scope="module")
def bell() -> str:
    return BELL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def users() -> str:
    return USERS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def endpoint() -> str:
    return ENDPOINT.read_text(encoding="utf-8")


def test_backend_notifies_owner_and_applicant(endpoint: str) -> None:
    assert "_notify_access_request" in endpoint
    assert 'kind="created"' in endpoint
    assert 'kind="decided"' in endpoint
    assert "PortalNotificationService" in endpoint


def test_notification_failure_never_breaks_the_flow(endpoint: str) -> None:
    block = endpoint.split("async def _notify_access_request", 1)[1].split(
        "@router.post", 1
    )[0]
    assert "except Exception" in block


def test_bell_labels_knowledge_access_messages(bell: str) -> None:
    assert 'item?.category === "knowledge_access"' in bell
    assert "知识库申请" in bell
    assert "申请结果" in bell


def _open_notification_body(bell: str) -> str:
    """只看点击处理，别命中分类标签那张映射表。"""
    return bell.split("const openNotification", 1)[1]


def test_reviewer_lands_on_the_review_dialog(bell: str) -> None:
    body = _open_notification_body(bell)
    block = body.split('if (item?.category === "knowledge_access")', 1)[1].split(
        'if (item?.category === "knowledge_access_result")', 1
    )[0]
    assert "/dashboard/users" in block
    assert 'review: "1"' in block


def test_applicant_lands_on_my_requests(bell: str) -> None:
    body = _open_notification_body(bell)
    block = body.split('if (item?.category === "knowledge_access_result")', 1)[1].split(
        "// 技能分享", 1
    )[0]
    assert "/dashboard/personal" in block
    assert 'tab: "requests"' in block


def test_users_page_opens_review_from_deep_link(users: str) -> None:
    assert 'route.query.review' in users
    assert "openAccessReviews()" in users


def test_review_entry_is_an_icon_before_create_user(users: str) -> None:
    """入口将来会并成统一审核中心，所以按钮只表达「有待处理」，不写死业务名。"""
    create_at = users.index("<!-- 创建用户：主操作放最后，与角色管理一致 -->")
    entry_at = users.index('aria-label="审核"')
    assert entry_at < create_at
    block = users[entry_at - 1200:entry_at + 1200]
    assert "openAccessReviews" in block
    assert "pendingReviewCount" in block
    # 图标按钮，不再是带文字的主按钮
    assert ">访问审批<" not in block
