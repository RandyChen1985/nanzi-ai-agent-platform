"""账号自主注册申请与管理员审核的后端行为测试。

覆盖：配置开关、状态常量、注册接口校验与限流、待审核登录拦截、
审核（PATCH status）与业务角色分配、待审核计数。

注意：本文件依赖共享 MySQL/Redis（tests/conftest.py 的 init_infrastructure）。
所有用例自带清理，避免污染共享库。
"""

import uuid

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.permission import Role, UserRoleRelation
from app.models.user import (
    USER_STATUS_DISABLED,
    USER_STATUS_ENABLED,
    USER_STATUS_PENDING_REVIEW,
    User,
)
from app.services.auth_service import AuthService
from app.services.config_service import (
    USER_REGISTRATION_ENABLED_KEY,
    ConfigService,
    validate_config_update,
)

REGISTER_URL = "/api/portal/auth/register"
CHECK_NAME_URL = "/api/portal/auth/register/available"
LOGIN_URL = "/api/portal/auth/login"
PENDING_COUNT_URL = "/api/portal/management/users/pending-count"
USERS_URL = "/api/portal/management/users"

#: 测试账号名前缀，便于按前缀清理
NAME_PREFIX = "zreg_"
VALID_PASSWORD = "Abcd1234!"


# --------------------------------------------------------------------------- #
# 基础设施辅助
#
# registration_on / registration_off / cleanup_registered 与 clear_register_limits
# 已上移到 tests/conftest.py：加邮箱字段时出现了第二个需要它们的测试文件，
# 而 tests/ 不是 package，测试模块之间无法 import 彼此的辅助代码。
# --------------------------------------------------------------------------- #

async def _cleanup_users(*user_names: str) -> None:
    async with AsyncSessionLocal() as session:
        ids = (
            await session.execute(select(User.id).where(User.user_name.in_(user_names)))
        ).scalars().all()
        if ids:
            await session.execute(
                delete(UserRoleRelation).where(UserRoleRelation.user_id.in_(ids))
            )
        await session.execute(delete(User).where(User.user_name.in_(user_names)))
        await session.commit()


def _new_name(suffix: str = "") -> str:
    return f"{NAME_PREFIX}{uuid.uuid4().hex[:12]}{suffix}"


async def _seed_user(name: str, status: int, real_name: str = "已存在") -> int:
    """建一个带 API Key 的账号（库里 api_key_hash 是 NOT NULL），返回 user_id。"""
    async with AsyncSessionLocal() as session:
        await AuthService.generate_api_key(
            name, real_name=real_name, status=status, db=session
        )
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()


def _payload(user_name: str, **overrides) -> dict:
    body = {
        "user_name": user_name,
        "real_name": "注册测试用户",
        "password": VALID_PASSWORD,
        "remark": "pytest 注册用例",
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# Task 2：状态常量与配置校验（纯单元，不需要基础设施）
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
def test_status_constants_are_stable():
    assert USER_STATUS_DISABLED == 0
    assert USER_STATUS_ENABLED == 1
    assert USER_STATUS_PENDING_REVIEW == 2


@pytest.mark.no_infrastructure
@pytest.mark.parametrize("value", ["true", "false", "1", "0", "yes", "no", "on", "off"])
def test_registration_switch_accepts_boolean_literals(value):
    validate_config_update(USER_REGISTRATION_ENABLED_KEY, value)


@pytest.mark.no_infrastructure
@pytest.mark.parametrize("value", ["", "2", "yes-please", "TRUE!"])
def test_registration_switch_rejects_other_values(value):
    with pytest.raises(ValueError):
        validate_config_update(USER_REGISTRATION_ENABLED_KEY, value)


# --------------------------------------------------------------------------- #
# Task 3：auth_service
# --------------------------------------------------------------------------- #

async def test_pending_user_password_login_is_rejected_with_review_message(cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    async with AsyncSessionLocal() as session:
        # 走 generate_api_key 建号：库里 api_key_hash 是 NOT NULL，直接 new User 会插入失败
        await AuthService.generate_api_key(
            name,
            real_name="待审核登录探针",
            status=USER_STATUS_PENDING_REVIEW,
            db=session,
        )
    async with AsyncSessionLocal() as session:
        user_id = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()
        await AuthService.set_user_password(user_id, VALID_PASSWORD, db=session)

    async with AsyncSessionLocal() as session:
        result = await AuthService.verify_user_password(name, VALID_PASSWORD, db=session)

    assert result["status"] == "pending_review"
    assert "审核" in result["message"]


async def test_generate_api_key_honours_explicit_status(cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)

    async with AsyncSessionLocal() as session:
        await AuthService.generate_api_key(name, status=USER_STATUS_PENDING_REVIEW, db=session)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(User).where(User.user_name == name))
        ).scalar_one()
    assert row.status == USER_STATUS_PENDING_REVIEW


async def test_generate_api_key_defaults_to_enabled(cleanup_registered):
    """默认参数必须保持既有调用方行为不变。"""
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)

    async with AsyncSessionLocal() as session:
        await AuthService.generate_api_key(name, db=session)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(User).where(User.user_name == name))
        ).scalar_one()
    assert row.status == USER_STATUS_ENABLED


# --------------------------------------------------------------------------- #
# Task 4：注册接口
# --------------------------------------------------------------------------- #

async def test_register_rejected_when_switch_off(client, registration_off):
    resp = await client.post(REGISTER_URL, json=_payload(_new_name()))
    assert resp.status_code == 403
    assert "未开放" in resp.json()["detail"]


async def test_register_succeeds_as_pending_review(client, registration_on, cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)

    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(User).where(User.user_name == name))
        ).scalar_one()
    assert row.status == USER_STATUS_PENDING_REVIEW
    assert row.role == "user"
    assert row.real_name == "注册测试用户"
    assert row.remark == "pytest 注册用例"
    assert row.password_hash
    assert row.api_key_hash  # 必须签发 API Key，否则审核通过后登录会 500
    assert row.password_updated_at is not None


async def test_register_response_does_not_leak_identity(client, registration_on, cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)

    resp = await client.post(REGISTER_URL, json=_payload(name))
    body = resp.json()
    assert "api_key" not in body
    assert name not in str(body)


@pytest.mark.parametrize(
    "bad_name",
    ["ab", "1abcdef", "ab cd", "abcdef!", "a" * 33],
)
async def test_register_rejects_malformed_user_name(
    client, registration_on, bad_name
):
    resp = await client.post(REGISTER_URL, json=_payload(bad_name))
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "bad_password,expected_fragment",
    [
        ("Ab1!", "8 到 32"),
        ("abcdefgh", "复杂度"),
        ("Abcd 1234", "空格"),
    ],
)
async def test_register_rejects_weak_password(
    client, registration_on, bad_password, expected_fragment
):
    name = _new_name()
    resp = await client.post(REGISTER_URL, json=_payload(name, password=bad_password))
    assert resp.status_code == 400
    assert expected_fragment in resp.json()["detail"]


async def test_register_rejects_password_containing_user_name(client, registration_on):
    name = _new_name()
    # 密码里嵌入完整账号名（用户名长度 >= 3 时生效）
    resp = await client.post(
        REGISTER_URL, json=_payload(name, password=f"{name}Ab1!")
    )
    assert resp.status_code == 400
    assert "用户名" in resp.json()["detail"]


async def test_register_rejects_blank_real_name(client, registration_on):
    resp = await client.post(REGISTER_URL, json=_payload(_new_name(), real_name="   "))
    assert resp.status_code == 400


async def test_register_rejects_overlong_remark(client, registration_on):
    resp = await client.post(REGISTER_URL, json=_payload(_new_name(), remark="备" * 256))
    assert resp.status_code == 400


@pytest.mark.parametrize("existing_status", [1, 0, 2])
async def test_register_rejects_duplicate_name_in_any_status(
    client, registration_on, cleanup_registered, existing_status
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await _seed_user(name, existing_status)

    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 400
    assert "已被占用" in resp.json()["detail"]


async def test_register_duplicate_check_is_case_insensitive(
    client, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await _cleanup_users(name.lower())
    await _seed_user(name.lower(), USER_STATUS_ENABLED, real_name="小写")

    resp = await client.post(REGISTER_URL, json=_payload(name.upper()))
    assert resp.status_code == 400
    assert "已被占用" in resp.json()["detail"]
    await _cleanup_users(name.lower())


async def test_register_rate_limit_by_user_name(
    client, registration_on, cleanup_registered
):
    """同一账号名重复提交超过阈值后返回 429。"""
    name = _new_name()
    cleanup_registered.append(name)  # 第 1 次会真的建号，必须清理
    statuses = []
    for _ in range(AuthService.REGISTER_NAME_LIMIT + 1):
        resp = await client.post(REGISTER_URL, json=_payload(name))
        statuses.append(resp.status_code)
    assert statuses[0] == 200
    assert statuses[-1] == 429


async def test_pending_user_cannot_login_via_password(client, registration_on, cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    resp = await client.post(LOGIN_URL, json={"username": name, "password": VALID_PASSWORD})
    assert resp.status_code == 403
    assert "审核" in resp.json()["detail"]


async def test_public_config_exposes_registration_switch(client, registration_on):
    resp = await client.get("/api/portal/auth/config/public")
    assert resp.status_code == 200
    assert resp.json()["data"]["user_registration_enabled"] is True


async def test_public_config_reports_disabled(client, registration_off):
    resp = await client.get("/api/portal/auth/config/public")
    assert resp.json()["data"]["user_registration_enabled"] is False


# --------------------------------------------------------------------------- #
# Task 5：用户管理端点
# --------------------------------------------------------------------------- #

async def test_update_status_rejects_unknown_value(client, admin_api_key, cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    user_id = await _seed_user(name, USER_STATUS_ENABLED, real_name="状态校验")

    resp = await client.patch(
        f"{USERS_URL}/{user_id}/status",
        json={"status": 3},
        headers={"X-API-Key": admin_api_key},
    )
    assert resp.status_code == 400


async def test_approve_pending_user_enables_login(
    client, admin_api_key, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    async with AsyncSessionLocal() as session:
        user_id = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()

    approve = await client.patch(
        f"{USERS_URL}/{user_id}/status",
        json={"status": USER_STATUS_ENABLED},
        headers={"X-API-Key": admin_api_key},
    )
    assert approve.status_code == 200, approve.text

    login = await client.post(LOGIN_URL, json={"username": name, "password": VALID_PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["status"] == "success"


async def test_reject_pending_user_blocks_login_and_keeps_name_taken(
    client, admin_api_key, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    async with AsyncSessionLocal() as session:
        user_id = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()

    reject = await client.patch(
        f"{USERS_URL}/{user_id}/status",
        json={"status": USER_STATUS_DISABLED},
        headers={"X-API-Key": admin_api_key},
    )
    assert reject.status_code == 200, reject.text

    # 禁用后走登录「失败」分支（与既有被禁用账号一致，返回 401 + 「账户已被禁用」），
    # 与待审核的 403「审核中」刻意区分开。
    login = await client.post(LOGIN_URL, json={"username": name, "password": VALID_PASSWORD})
    assert login.status_code == 401
    assert "禁用" in login.json()["detail"]

    # 账号名不释放：重新申请仍然被拒
    again = await client.post(REGISTER_URL, json=_payload(name))
    assert again.status_code == 400
    assert "已被占用" in again.json()["detail"]


async def test_approve_with_role_ids_assigns_business_roles(
    client, admin_api_key, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    async with AsyncSessionLocal() as session:
        user_id = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()
        role = Role(name=f"zreg_role_{uuid.uuid4().hex[:8]}", code=f"zreg{uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.commit()
        role_id = role.id

    try:
        resp = await client.patch(
            f"{USERS_URL}/{user_id}/status",
            json={"status": USER_STATUS_ENABLED, "role_ids": [role_id]},
            headers={"X-API-Key": admin_api_key},
        )
        assert resp.status_code == 200, resp.text

        async with AsyncSessionLocal() as session:
            assigned = (
                await session.execute(
                    select(UserRoleRelation.role_id).where(
                        UserRoleRelation.user_id == user_id
                    )
                )
            ).scalars().all()
        assert list(assigned) == [role_id]
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(UserRoleRelation).where(UserRoleRelation.role_id == role_id))
            await session.execute(delete(Role).where(Role.id == role_id))
            await session.commit()


async def test_approve_with_unknown_role_id_returns_400(
    client, admin_api_key, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    async with AsyncSessionLocal() as session:
        user_id = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()

    resp = await client.patch(
        f"{USERS_URL}/{user_id}/status",
        json={"status": USER_STATUS_ENABLED, "role_ids": [999999999]},
        headers={"X-API-Key": admin_api_key},
    )
    assert resp.status_code == 400


async def test_approve_without_role_ids_leaves_roles_untouched(
    client, admin_api_key, registration_on, cleanup_registered
):
    """role_ids 未传（None）表示不触碰角色，不能因审核把已有角色清空。"""
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    async with AsyncSessionLocal() as session:
        user_id = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).scalar_one()
        role = Role(name=f"zreg_role_{uuid.uuid4().hex[:8]}", code=f"zreg{uuid.uuid4().hex[:8]}")
        session.add(role)
        await session.commit()
        role_id = role.id
        session.add(UserRoleRelation(user_id=user_id, role_id=role_id))
        await session.commit()

    try:
        resp = await client.patch(
            f"{USERS_URL}/{user_id}/status",
            json={"status": USER_STATUS_DISABLED},
            headers={"X-API-Key": admin_api_key},
        )
        assert resp.status_code == 200, resp.text
        async with AsyncSessionLocal() as session:
            assigned = (
                await session.execute(
                    select(UserRoleRelation.role_id).where(
                        UserRoleRelation.user_id == user_id
                    )
                )
            ).scalars().all()
        assert list(assigned) == [role_id]
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(UserRoleRelation).where(UserRoleRelation.role_id == role_id))
            await session.execute(delete(Role).where(Role.id == role_id))
            await session.commit()


async def test_pending_count_endpoint_reports_registered_users(
    client, admin_api_key, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)

    before = (
        await client.get(PENDING_COUNT_URL, headers={"X-API-Key": admin_api_key})
    ).json()["count"]
    await client.post(REGISTER_URL, json=_payload(name))
    after = (
        await client.get(PENDING_COUNT_URL, headers={"X-API-Key": admin_api_key})
    ).json()["count"]
    assert after == before + 1


async def test_list_users_filters_pending_review(
    client, admin_api_key, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await client.post(REGISTER_URL, json=_payload(name))

    resp = await client.get(
        USERS_URL, params={"status": USER_STATUS_PENDING_REVIEW, "search": name},
        headers={"X-API-Key": admin_api_key},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["user_name"] for item in items] == [name]
    assert items[0]["status"] == USER_STATUS_PENDING_REVIEW


# --------------------------------------------------------------------------- #
# 来源地址可信度（纯单元，不需要基础设施）
#
# request.client.host 是 TCP 对端而非用户 IP；反代在异机/异 Pod 时它是反代自身，
# 照用会让全平台共用一个限流计数器。这组用例钉住「宁可不限，也不误伤」的判定。
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "client_host,forwarded_for",
    [
        ("127.0.0.1", None),            # 本机回环
        ("10.244.3.9", None),           # k8s Pod 网段（ingress 未设 XFF 时唯一能拦住的一道网）
        ("192.168.1.50", None),         # 私网
        ("172.16.8.4", None),           # 私网
        ("100.64.0.7", None),           # CGNAT
        ("169.254.1.1", None),          # 链路本地
        ("::1", None),                  # IPv6 回环
        ("not-an-ip", None),            # unix socket / 测试替身
        ("", None),
        (None, None),
    ],
)
def test_private_or_non_ip_source_is_never_trusted(client_host, forwarded_for):
    assert AuthService._is_trustworthy_source(client_host, forwarded_for) is False


@pytest.mark.no_infrastructure
def test_public_source_without_proxy_trace_is_trusted():
    """无反代时对端就是客户端本身。"""
    assert AuthService._is_trustworthy_source("8.8.8.8", None) is True
    assert AuthService._is_trustworthy_source("8.8.8.8", "") is True
    assert AuthService._is_trustworthy_source("2001:4860:4860::8888", "") is True


@pytest.mark.no_infrastructure
def test_untrusted_proxy_address_is_not_trusted_even_if_public():
    """反代在异机：client_host 是反代自己的公网地址，且不在 XFF 链里 → 不可信。

    这是「全平台共用一个桶」的另一种形态（云 LB / CDN 有公网地址），
    只靠「必须是公网地址」拦不住，必须靠 XFF 链归属判定。
    """
    assert AuthService._is_trustworthy_source("8.8.8.8", "203.0.113.9") is False


@pytest.mark.no_infrastructure
def test_trusted_proxy_resolves_real_client_from_xff_chain():
    """反代可信时 uvicorn 已用 XFF 改写 client.host，它必然出现在 XFF 链里。"""
    assert AuthService._is_trustworthy_source("8.8.8.8", "8.8.8.8") is True
    # 客户端伪造的前缀不影响：真实客户端仍在链中
    assert AuthService._is_trustworthy_source("8.8.8.8", "1.2.3.4, 8.8.8.8") is True
    # 链路带端口 / IPv6 方括号也要能对上
    assert AuthService._is_trustworthy_source("8.8.8.8", "8.8.8.8:51234") is True
    assert (
        AuthService._is_trustworthy_source("2001:4860:4860::8888", "[2001:4860:4860::8888]:443")
        is True
    )


async def test_no_source_limit_means_name_dimension_still_applies(registration_on):
    """client_ip 为 None（来源不可信）时只按账号名限流，而不是彻底不限流。"""
    name = _new_name()
    for _ in range(AuthService.REGISTER_NAME_LIMIT):
        assert await AuthService.is_registration_rate_limited(None, name) is False
    assert await AuthService.is_registration_rate_limited(None, name) is True


# --------------------------------------------------------------------------- #
# 待审核积压上限（与来源/请求头/部署拓扑无关的兜底）
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
def test_pending_registration_limit_is_documented_and_reachable():
    assert isinstance(AuthService.REGISTER_PENDING_LIMIT, int)
    assert AuthService.REGISTER_PENDING_LIMIT > 0


def _fake_pending(count: int):
    async def _fake(db=None) -> int:
        return count

    return _fake


async def test_register_refused_when_pending_queue_is_full(
    client, registration_on, monkeypatch
):
    """积压触顶时拒绝新申请 —— 这一层不依赖来源判定，任何拓扑下都成立。"""
    monkeypatch.setattr(
        AuthService,
        "count_pending_registrations",
        staticmethod(_fake_pending(AuthService.REGISTER_PENDING_LIMIT)),
    )
    resp = await client.post(REGISTER_URL, json=_payload(_new_name()))
    assert resp.status_code == 429
    assert "过多" in resp.json()["detail"]


async def test_register_allowed_just_below_pending_limit(
    client, registration_on, cleanup_registered, monkeypatch
):
    monkeypatch.setattr(
        AuthService,
        "count_pending_registrations",
        staticmethod(_fake_pending(AuthService.REGISTER_PENDING_LIMIT - 1)),
    )
    name = _new_name()
    cleanup_registered.append(name)
    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 200, resp.text


async def test_register_counts_validation_failures_against_the_quota(
    client, registration_on
):
    """已确认的取舍：限流在校验之前，所以密码拼错也消耗额度。

    钉住它是因为这直接决定用户体验：填错几次就进 1 小时冷却。
    若将来改成「只有走到建号才计数」，这条用例会失败，提醒同步改文档与提示语。
    """
    name = _new_name()
    for i in range(AuthService.REGISTER_NAME_LIMIT):
        resp = await client.post(REGISTER_URL, json=_payload(name, password="weak"))
        assert resp.status_code == 400, f"第 {i + 1} 次应是密码复杂度 400：{resp.text}"

    # 额度已被失败的尝试吃满：这次密码合法也进不去
    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 429

    async with AsyncSessionLocal() as session:
        created = (
            await session.execute(select(User.id).where(User.user_name == name))
        ).first()
    assert created is None, "全程未建号，不应有残留"


async def test_name_dimension_does_not_starve_the_source_quota(registration_on):
    """同一账号名反复重试只吃账号名名额，不应把来源额度一起吃掉。

    账号名维度先判且命中即返回，所以重试同名不会继续自增来源计数——
    这修掉了「重试 3 次同名 + 换个名字只剩 1 次机会」的叠加问题。
    直接打 service 层以便用一个**可信公网地址**触发来源维度（tests 客户端是回环地址，不可信）。
    """
    source_ip = "8.8.8.8"
    name = _new_name()
    for _ in range(AuthService.REGISTER_NAME_LIMIT + 1):
        await AuthService.is_registration_rate_limited(source_ip, name)

    # 同名已命中账号名上限
    assert await AuthService.is_registration_rate_limited(source_ip, name) is True
    # 但来源额度只被消耗了 REGISTER_NAME_LIMIT 次，换个名字仍有额度
    assert await AuthService.is_registration_rate_limited(source_ip, _new_name()) is False


async def test_untrusted_source_never_creates_a_source_counter(
    client, registration_on
):
    """tests 客户端的对端是回环地址 → 来源维度不启用，Redis 里不该出现来源键。

    这正是修复的核心：反代在异机/异 Pod 时 client.host 是反代自身，
    若照旧限流会让全平台共用一个计数器。
    """
    await client.post(REGISTER_URL, json=_payload(_new_name(), password="weak"))

    redis = await get_redis()
    if not redis:  # pragma: no cover
        pytest.skip("Redis 不可用")
    keys = [k async for k in redis.scan_iter(match="auth:register:ip:*")]
    assert keys == [], f"不可信来源不应创建来源计数器，却出现了 {keys}"


# --------------------------------------------------------------------------- #
# 账号名可用性预检（边输边查）
#
# 这是「账号名是否存在」的判定器，用例同时钉住三重对冲：开关门禁、独立限流、
# 不回显占用者信息。口径必须与注册提交完全一致，否则预检说可用、提交却报重名。
# --------------------------------------------------------------------------- #

def _check_url(name: str) -> str:
    return f"{CHECK_NAME_URL}?user_name={name}"


async def test_check_name_rejected_when_switch_off(client, registration_off):
    """注册关闭时预检也必须不存在，否则等于留了个常开的用户名枚举口子。"""
    resp = await client.get(_check_url(_new_name()))
    assert resp.status_code == 403
    assert "未开放" in resp.json()["detail"]


async def test_check_name_reports_available_for_free_name(client, registration_on):
    resp = await client.get(_check_url(_new_name()))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["available"] is True
    assert data["reason"] == "ok"
    assert "可以使用" in data["message"]


async def test_check_name_reports_taken_for_existing_user(
    client, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await _seed_user(name, USER_STATUS_ENABLED)

    resp = await client.get(_check_url(name))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert data["reason"] == "taken"
    assert data["message"] == "该账号名已被占用，请更换后重试"


async def test_check_name_matches_register_verdict_in_every_status(
    client, registration_on, cleanup_registered
):
    """启用 / 禁用 / 待审核三种状态都必须判为「已占用」，与提交口径一致。"""
    for status in (USER_STATUS_ENABLED, USER_STATUS_DISABLED, USER_STATUS_PENDING_REVIEW):
        name = _new_name()
        cleanup_registered.append(name)
        await _cleanup_users(name)
        await _seed_user(name, status)

        check = await client.get(_check_url(name))
        assert check.json()["data"]["available"] is False, f"status={status} 预检漏判"

        submit = await client.post(REGISTER_URL, json=_payload(name))
        assert submit.status_code == 400
        assert submit.json()["detail"] == check.json()["data"]["message"]


async def test_check_name_is_case_insensitive(client, registration_on, cleanup_registered):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await _seed_user(name, USER_STATUS_ENABLED)

    resp = await client.get(_check_url(name.upper()))
    assert resp.status_code == 200
    assert resp.json()["data"]["reason"] == "taken"


async def test_check_name_reports_invalid_format_without_erroring(client, registration_on):
    """格式不合法走 200 + available=false：边输边查场景下「ab」是中间态，不该报错。"""
    resp = await client.get(_check_url("ab"))
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert data["reason"] == "invalid"
    assert "3–32 位" in data["message"]


async def test_check_name_does_not_leak_owner_identity(
    client, registration_on, cleanup_registered
):
    name = _new_name()
    cleanup_registered.append(name)
    await _cleanup_users(name)
    await _seed_user(name, USER_STATUS_ENABLED, real_name="真实姓名不应出现")

    resp = await client.get(_check_url(name))
    assert "真实姓名不应出现" not in resp.text
    body = resp.json()["data"]
    # 只回可判定字段，不回 id / status / real_name
    assert set(body) == {"available", "reason", "message"}


async def test_check_name_rate_limited_per_ip(client, registration_on, monkeypatch):
    """预检有独立且更严的限流（默认 60/小时），这里收紧阈值验证 429 生效。"""
    monkeypatch.setattr(AuthService, "REGISTER_CHECK_IP_LIMIT", 3)

    for _ in range(3):
        resp = await client.get(_check_url(_new_name()))
        assert resp.status_code == 200

    resp = await client.get(_check_url(_new_name()))
    assert resp.status_code == 429
    assert "过于频繁" in resp.json()["detail"]


async def test_check_name_does_not_consume_register_submit_quota(
    client, registration_on, cleanup_registered
):
    """两个计数器必须分离：多查几次不能把用户自己的提交额度吃掉。"""
    for _ in range(AuthService.REGISTER_IP_LIMIT):
        assert (await client.get(_check_url(_new_name()))).status_code == 200

    name = _new_name()
    cleanup_registered.append(name)
    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 200, resp.text
