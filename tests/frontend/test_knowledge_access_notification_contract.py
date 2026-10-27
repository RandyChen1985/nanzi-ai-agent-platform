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
SERVICE = ROOT / "app/services/resource_access_request_service.py"
LEGACY_ENDPOINT = ROOT / "app/api/portal/endpoints/ragflow.py"


@pytest.fixture(scope="module")
def bell() -> str:
    return BELL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def users() -> str:
    return USERS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def service() -> str:
    return SERVICE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def legacy_endpoint() -> str:
    return LEGACY_ENDPOINT.read_text(encoding="utf-8")


def test_backend_notifies_admins_and_applicant(service: str) -> None:
    """提交后通知管理员、审批后通知申请人。

    审批人从「库归属人」改为「管理员」之后，提交通知也要跟着换收件人——
    只改审批入口不改通知，申请会堆在一个没人去看的地方。
    """
    assert "PortalNotificationService" in service
    assert "_notify_created" in service
    assert "_notify_decided" in service
    assert "_admin_user_ids" in service
    assert '"resource_access"' in service
    assert '"resource_access_result"' in service


def test_notification_failure_never_breaks_the_flow(service: str) -> None:
    """通知失败不能把审批/提交一起回滚——主流程已经完成了。"""
    block = service.split("async def _notify_created", 1)[1]
    assert "except Exception" in block


def test_legacy_endpoints_do_not_notify_twice(legacy_endpoint: str) -> None:
    """旧端点只做转发：再发一次通知/再清一次缓存，副作用就重复了。

    手工授权/撤销端点里的缓存清理是它们自己的职责，不在检查范围内——
    只检查审批端点有没有自己再来一遍。
    """
    assert "_notify_access_request" not in legacy_endpoint
    for name, following in (
        ("approve_access_request", "reject_access_request"),
        ("reject_access_request", "cancel_access_request"),
    ):
        block = legacy_endpoint.split(f"async def {name}", 1)[1].split(
            f"async def {following}", 1
        )[0]
        assert "invalidate_cached_permissions_for_users" not in block
        assert "record_knowledge_audit" not in block


def test_bell_labels_resource_access_messages(bell: str) -> None:
    assert 'item?.category === "resource_access"' in bell
    assert "资源申请" in bell
    assert "申请结果" in bell
    # 历史消息还带旧分类，分支不能删
    assert 'item?.category === "knowledge_access"' in bell


def _open_notification_body(bell: str) -> str:
    """只看点击处理，别命中分类标签那张映射表。"""
    return bell.split("const openNotification", 1)[1]


def test_reviewer_lands_on_the_review_dialog(bell: str) -> None:
    body = _open_notification_body(bell)
    block = body.split('if (item?.category === "resource_access" ||', 1)[1].split(
        'if (', 1
    )[0]
    assert "/dashboard/users" in block
    assert 'review: "1"' in block
    assert "request: String(meta.request_id" in block


def test_applicant_lands_on_my_requests(bell: str) -> None:
    body = _open_notification_body(bell)
    block = body.split('item?.category === "resource_access_result"', 1)[1].split(
        "// 技能分享", 1
    )[0]
    assert "/dashboard/personal" in block
    assert 'tab: "permissions"' in block
    assert 'subtab: "requests"' in block


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
