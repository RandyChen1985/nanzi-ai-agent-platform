"""用户邮箱字段与邮件服务的行为测试。

约定：纯逻辑用例标 no_infrastructure；涉及库的用例复用 conftest 的 client/db_session。
"""
import asyncio
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.permission import UserRoleRelation
from app.models.user import (
    USER_STATUS_ENABLED,
    USER_STATUS_PENDING_REVIEW,
    User,
)
from app.services.auth_service import AuthService

pytestmark = []


# --------------------------------------------------------------------------- #
# 邮箱归一化（纯单元）
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  User@Example.COM  ", "user@example.com"),
        ("a.b+c@sub.domain.cn", "a.b+c@sub.domain.cn"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_email_normalizes_or_blanks(raw, expected):
    assert AuthService.normalize_email(raw) == expected


@pytest.mark.no_infrastructure
@pytest.mark.parametrize(
    "raw",
    [
        "no-at-sign",
        "no-dot@localhost",
        "two@@at.com",
        "has space@example.com",
        "@example.com",
        "user@",
        "user@" + "a" * 250 + ".com",   # 超长
    ],
)
def test_normalize_email_raises_on_invalid(raw):
    with pytest.raises(ValueError):
        AuthService.normalize_email(raw, strict=True)


@pytest.mark.no_infrastructure
def test_normalize_email_lenient_mode_returns_none_for_invalid():
    """非 strict 模式用于「用户还没输完」的场景，不抛异常。"""
    assert AuthService.normalize_email("no-at-sign") is None


# --------------------------------------------------------------------------- #
# 自助注册支持邮箱（任务 3）
# --------------------------------------------------------------------------- #

from app.services.auth_service import AuthService as _AS

REGISTER_URL = "/api/portal/auth/register"


def _payload(name, email=None, password="Str0ng!Passw0rd"):
    body = {"user_name": name, "real_name": "邮箱测试", "password": password}
    if email is not None:
        body["email"] = email
    return body


async def test_register_accepts_optional_email(
    client, registration_on, cleanup_registered
):
    name = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.append(name)
    email = f"{name}@Example.COM"          # 大写 + 混合，验证归一化
    resp = await client.post(REGISTER_URL, json=_payload(name, email))
    assert resp.status_code == 200, resp.text

    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(User.email).where(User.user_name == name))).scalar_one()
    assert row == f"{name}@example.com".lower(), "必须小写归一化后落库"


async def test_register_without_email_still_works(
    client, registration_on, cleanup_registered
):
    name = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.append(name)
    resp = await client.post(REGISTER_URL, json=_payload(name))
    assert resp.status_code == 200, resp.text


async def test_register_rejects_invalid_email(client, registration_on):
    resp = await client.post(REGISTER_URL, json=_payload("zmail_bad", "not-an-email"))
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"]


async def test_register_rejects_duplicate_email_case_insensitively(
    client, registration_on, cleanup_registered
):
    email = f"dup_{uuid.uuid4().hex[:8]}@example.com"
    first = f"zmail_{uuid.uuid4().hex[:8]}"
    second = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.extend([first, second])

    assert (await client.post(REGISTER_URL, json=_payload(first, email))).status_code == 200
    resp = await client.post(REGISTER_URL, json=_payload(second, email.upper()))
    assert resp.status_code == 400
    assert "邮箱" in resp.json()["detail"], "必须是邮箱文案，不能误报成账号名冲突"


async def test_email_conflict_is_not_reported_as_user_name_conflict(
    client, registration_on, cleanup_registered
):
    """钉住最容易写错的一处：邮箱撞唯一索引时不能报「账号名已被占用」。"""
    email = f"dup2_{uuid.uuid4().hex[:8]}@example.com"
    first = f"zmail_{uuid.uuid4().hex[:8]}"
    second = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.extend([first, second])
    assert (await client.post(REGISTER_URL, json=_payload(first, email))).status_code == 200

    resp = await client.post(REGISTER_URL, json=_payload(second, email))
    detail = resp.json()["detail"]
    assert "邮箱" in detail
    assert "账号名" not in detail


# --------------------------------------------------------------------------- #
# 任务 4：管理员创建/编辑支持邮箱
# --------------------------------------------------------------------------- #

USERS_URL = "/api/portal/management/users"


async def _cleanup(uid):
    """清理单个用户（含角色关联）。

    计划原文用 `asyncio.run(...)` 包同步函数，但用例本身跑在 pytest-asyncio
    的事件循环里，`asyncio.run()` 会直接抛 RuntimeError；因此这里改为 async 助手，
    调用处以 `await _cleanup(uid)` 使用。清理语义与 `cleanup_registered` 一致。
    """
    if uid is None:
        return
    async with AsyncSessionLocal() as s:
        await s.execute(delete(UserRoleRelation).where(UserRoleRelation.user_id == uid))
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()


async def _uid_of(user_name):
    """按账号名查 id；用于断言失败时的兜底清理（那时还没拿到响应里的 id）。"""
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(User.id).where(User.user_name == user_name))).scalar()


async def test_admin_create_user_with_email(client, admin_api_key):
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        resp = await client.post(
            USERS_URL,
            json={"user_name": name, "real_name": "管理员建", "email": email.upper()},
            headers={"X-API-Key": admin_api_key},
        )
        assert resp.status_code == 200, resp.text
        uid = resp.json().get("id") or resp.json().get("user_id")
        async with AsyncSessionLocal() as s:
            stored = (await s.execute(select(User.email).where(User.id == uid))).scalar_one()
        assert stored == email.lower()
    finally:
        # 兜底清理放在 finally：断言失败时也不会在共享库里留下残留账号
        await _cleanup(await _uid_of(name))


async def test_admin_create_rejects_duplicate_email(client, admin_api_key, db_session):
    email = f"adup_{uuid.uuid4().hex[:8]}@corp.example.com"
    a = f"zma_{uuid.uuid4().hex[:8]}"
    b = f"zma_{uuid.uuid4().hex[:8]}"
    try:
        ra = await client.post(USERS_URL, json={"user_name": a, "email": email},
                               headers={"X-API-Key": admin_api_key})
        assert ra.status_code == 200
        rb = await client.post(USERS_URL, json={"user_name": b, "email": email},
                               headers={"X-API-Key": admin_api_key})
        assert rb.status_code == 400
        assert "邮箱" in rb.json()["detail"]
    finally:
        await _cleanup(await _uid_of(a))
        await _cleanup(await _uid_of(b))


async def test_admin_edit_keeps_own_email_without_conflict(client, admin_api_key):
    """编辑自己时未改邮箱不能判成重复。"""
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        r = await client.post(USERS_URL, json={"user_name": name, "email": email},
                              headers={"X-API-Key": admin_api_key})
        uid = r.json().get("id") or r.json().get("user_id")
        upd = await client.put(f"{USERS_URL}/{uid}", json={"real_name": "改名", "email": email},
                               headers={"X-API-Key": admin_api_key})
        assert upd.status_code == 200, upd.text
    finally:
        await _cleanup(await _uid_of(name))


# --------------------------------------------------------------------------- #
# 任务 5：列表序列化 / 按邮箱搜索 / /me 返回 email
# --------------------------------------------------------------------------- #

async def test_user_list_returns_email_and_searches_by_it(client, admin_api_key):
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"findme_{uuid.uuid4().hex[:8]}@corp.example.com"
    try:
        r = await client.post(USERS_URL, json={"user_name": name, "email": email},
                              headers={"X-API-Key": admin_api_key})
        assert r.status_code == 200, r.text

        listing = await client.get(USERS_URL, params={"search": email},
                                   headers={"X-API-Key": admin_api_key})
        assert listing.status_code == 200
        items = listing.json().get("items", listing.json())
        assert any(i["email"] == email for i in items), "必须能按邮箱搜索并返回 email 字段"
    finally:
        await _cleanup(await _uid_of(name))


async def test_me_returns_email(client, valid_api_key, db_session):
    resp = await client.get("/api/portal/auth/me", headers={"X-API-Key": valid_api_key})
    assert resp.status_code == 200
    assert "email" in resp.json(), "/me 必须返回 email（AuthService 里 5 处 dict 都要补）"


# --------------------------------------------------------------------------- #
# 任务 6：个人中心自助编辑邮箱
# --------------------------------------------------------------------------- #

PROFILE_URL = "/api/portal/auth/me/profile"


async def _seed_user(user_name, email=None):
    """建一个启用状态的用户并返回其明文 API Key。

    注意 generate_api_key 的返回语义：它内部生成 api_key/encrypted/hash 并自行落库，
    只把**明文 key** 返回给调用方（签名标注 -> str）。早期计划版本误写成
    `api_key, key_hash, encrypted = ...` 三元组解构，与实际不符。
    """
    async with AsyncSessionLocal() as s:
        return await AuthService.generate_api_key(
            user_name, real_name="自助测试", status=1, email=email, db=s
        )


async def _drop_user(user_name):
    async with AsyncSessionLocal() as s:
        uid = (await s.execute(select(User.id).where(User.user_name == user_name))).scalar()
        if uid is not None:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


async def test_user_can_update_own_email(client, db_session):
    name = f"zself_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        api_key = await _seed_user(name, email=email)
        resp = await client.patch(PROFILE_URL, json={"email": email.upper()},
                                  headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert resp.json()["email"] == email
    finally:
        await _drop_user(name)


async def test_user_cannot_take_another_users_email(client, db_session):
    taken = f"taken_{uuid.uuid4().hex[:8]}@corp.example.com"
    owner = f"zself_{uuid.uuid4().hex[:8]}"
    other = f"zself_{uuid.uuid4().hex[:8]}"
    try:
        await _seed_user(owner, email=taken)
        other_key = await _seed_user(other)
        resp = await client.patch(PROFILE_URL, json={"email": taken},
                                  headers={"X-API-Key": other_key})
        assert resp.status_code == 400
        assert "邮箱" in resp.json()["detail"]
    finally:
        await _drop_user(owner)
        await _drop_user(other)


async def test_user_can_clear_own_email(client, db_session):
    name = f"zself_{uuid.uuid4().hex[:8]}"
    try:
        key = await _seed_user(name, email=f"{name}@corp.example.com")
        resp = await client.patch(PROFILE_URL, json={"email": None},
                                  headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()["email"] == ""
    finally:
        await _drop_user(name)


async def test_profile_endpoint_requires_api_key(client):
    """auth.router 是唯一没有整组鉴权的 router，这个端点必须显式挂 require_api_key。

    漏挂依赖时端点会变成公开接口（能任意改别人的邮箱），所以单独立一条用例钉住。
    """
    resp = await client.patch(PROFILE_URL, json={"email": "nobody@corp.example.com"})
    assert resp.status_code == 401, "缺少 require_api_key 就是公开接口"


async def test_profile_endpoint_ignores_other_fields(client, db_session):
    """最小权限：只接受 email，real_name/role/status 一律不生效。"""
    name = f"zself_{uuid.uuid4().hex[:8]}"
    try:
        key = await _seed_user(name)
        resp = await client.patch(
            PROFILE_URL,
            json={"email": f"{name}@corp.example.com", "real_name": "篡改", "role": "admin"},
            headers={"X-API-Key": key},
        )
        assert resp.status_code == 200
        async with AsyncSessionLocal() as s:
            row = (await s.execute(
                select(User.real_name, User.role).where(User.user_name == name)
            )).one()
        assert row[0] != "篡改" and row[1] != "admin", "其他字段必须被忽略"
    finally:
        await _drop_user(name)


# --------------------------------------------------------------------------- #
# 邮件服务配置校验（纯单元）
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
def test_email_config_validation_rules():
    from app.services.config_service import validate_config_update

    validate_config_update("email_service_enabled", "true")
    validate_config_update("email_smtp_port", "587")
    validate_config_update("email_smtp_port", "")      # 允许未填写
    validate_config_update("email_smtp_security", "none")
    validate_config_update("email_from_address", "")   # 允许未填写
    validate_config_update("email_from_address", "a@b.cn")

    for key, bad in [
        ("email_service_enabled", "maybe"),
        ("email_smtp_port", "70000"),
        ("email_smtp_port", "abc"),
        ("email_smtp_security", "tls"),
        ("email_from_address", "not-an-email"),
    ]:
        with pytest.raises(ValueError):
            validate_config_update(key, bad)


# --------------------------------------------------------------------------- #
# 邮件发送服务（纯单元）
# --------------------------------------------------------------------------- #

from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings


@pytest.mark.no_infrastructure
def test_smtp_settings_completeness_rule():
    # host 是唯一必需项：内部中继免认证，user/password 允许同时为空
    assert SmtpSettings(host="smtp.a.com", port=25, user="", password="",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is True
    assert SmtpSettings(host="", port=25, user="", password="",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is False
    # 只填了 user 或只填了 password：半套凭据几乎总是配置失误，必须判为不完整
    assert SmtpSettings(host="h", port=25, user="u", password="",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is False
    assert SmtpSettings(host="h", port=25, user="", password="p",
                        security="none", from_address="a@b.cn",
                        sender_name="X").is_complete() is False


@pytest.mark.no_infrastructure
def test_smtp_settings_effective_from_falls_back_to_user():
    s = SmtpSettings(host="h", port=25, user="login@a.com", password="",
                     security="none", from_address="", sender_name="X")
    assert s.effective_from() == "login@a.com"
    s2 = SmtpSettings(host="h", port=25, user="login@a.com", password="",
                      security="none", from_address="from@a.com", sender_name="X")
    assert s2.effective_from() == "from@a.com"


@pytest.mark.no_infrastructure
def test_parse_recipients_dedupes_and_strips():
    got = EmailDeliveryService.parse_recipients("a@x.com, B@Y.com ; a@x.com ,,")
    assert got == ["a@x.com", "B@Y.com"]


@pytest.mark.no_infrastructure
def test_compose_recipients_includes_self_by_default():
    got = EmailDeliveryService.compose_recipients(
        self_email="me@x.com", include_self=True, extra="other@x.com"
    )
    assert got == ["me@x.com", "other@x.com"]


@pytest.mark.no_infrastructure
def test_compose_recipients_dedupes_self_when_already_in_extra():
    """存量用户 recipients 里已经写了自己时不能重复收两封。"""
    got = EmailDeliveryService.compose_recipients(
        self_email="me@x.com", include_self=True, extra="me@x.com, other@x.com"
    )
    assert got == ["me@x.com", "other@x.com"]


@pytest.mark.no_infrastructure
def test_compose_recipients_without_self_or_email():
    assert EmailDeliveryService.compose_recipients(
        self_email=None, include_self=True, extra="other@x.com"
    ) == ["other@x.com"]
    assert EmailDeliveryService.compose_recipients(
        self_email="me@x.com", include_self=False, extra=""
    ) == []


@pytest.mark.no_infrastructure
def test_resolve_smtp_source_legacy_fallback():
    """存量兼容：缺 smtp_source 时按 smtp_host 是否已填自适应。"""
    assert EmailDeliveryService.resolve_smtp_source({"smtp_host": "smtp.a.com"}) == "custom"
    assert EmailDeliveryService.resolve_smtp_source({"smtp_host": ""}) == "global"
    assert EmailDeliveryService.resolve_smtp_source({}) == "global"
    assert EmailDeliveryService.resolve_smtp_source({"smtp_source": "global",
                                                     "smtp_host": "smtp.a.com"}) == "global"
    assert EmailDeliveryService.resolve_smtp_source({"smtp_source": "custom"}) == "custom"


@pytest.mark.no_infrastructure
def test_sanitize_error_text_never_leaks_password(monkeypatch):
    leaked = "auth failed for user u with password Sup3rSecret!"
    cleaned = EmailDeliveryService.sanitize_error_text(leaked, "Sup3rSecret!")
    assert "Sup3rSecret" not in cleaned
    assert "u" in cleaned


# --------------------------------------------------------------------------- #
# 测试发送端点
# --------------------------------------------------------------------------- #

EMAIL_TEST_URL = "/api/portal/system/configs/email/test"


async def test_email_test_endpoint_requires_permission(client, valid_api_key):
    """普通用户没有 element:system:config_save，必须被拒。"""
    resp = await client.post(EMAIL_TEST_URL, json={}, headers={"X-API-Key": valid_api_key})
    assert resp.status_code in (401, 403)


async def test_email_test_endpoint_reports_disabled_service(client, admin_api_key, monkeypatch):
    """未启用全局服务时必须明确报错，而不是模糊的失败。

    这里**必须**把全局设置打成 None：否则一旦开发库按功能文档启用了邮件服务
    （那正是本功能的正常使用状态），该接口会向发件人地址**真实发出一封测试邮件**，
    随后 assert ok is False 才失败——既是环境耦合，也是一次真实副作用。
    """
    from app.services import email_delivery_service as mod

    async def _disabled():
        return None

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _disabled)

    resp = await client.post(EMAIL_TEST_URL, json={}, headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "未启用" in body["message"] or "配置不完整" in body["message"]


async def test_email_test_endpoint_never_leaks_password(client, admin_api_key, monkeypatch):
    """错误文本里出现密码时必须在回传前抹掉。"""
    from app.services import email_delivery_service as mod

    settings = mod.SmtpSettings(host="bad.invalid", port=25, user="u",
                                password="Sup3rSecret!", security="none",
                                from_address="f@a.cn", sender_name="X")
    async def _fake_global():
        return settings
    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _fake_global)

    resp = await client.post(EMAIL_TEST_URL, json={"to": "t@a.cn"},
                             headers={"X-API-Key": admin_api_key})
    assert resp.status_code == 200
    assert "Sup3rSecret" not in resp.text


# --------------------------------------------------------------------------- #
# 任务 12：审核邮件文案
# --------------------------------------------------------------------------- #


@pytest.mark.no_infrastructure
def test_review_mail_subject_and_body_reflect_the_outcome():
    from app.services.email_delivery_service import build_review_mail

    subject, body = build_review_mail("zhangsan", approved=True, sender_name="NanZi")
    assert "启用" in subject and "NanZi" in subject
    assert "zhangsan" in body
    assert "通过审核" in body
    assert "管理员" in body, "要给出下一步求助路径，否则用户被卡住无从下手"

    subject2, body2 = build_review_mail("zhangsan", approved=False, sender_name="NanZi")
    assert "未通过" in subject2
    assert "zhangsan" in body2
    assert "管理员" in body2


@pytest.mark.no_infrastructure
def test_review_mail_cannot_leak_credentials():
    """审核邮件不得泄露凭据——从**来源**上保证，而不是靠禁用某个词。

    早先这里的断言是 `assert "密码" not in body`，它禁掉的是「密码」这个**词**：
    结果连「如忘记密码，请联系管理员重置」这种正当提示也被禁了，逼着文案改用
    「登录口令」去绕开断言——测试反而在推着实现变差。

    真正要守的性质是：这个函数**根本拿不到**任何凭据。所以断言它的入参只有
    账号名、审核结果、发件人名称。将来若有人为了「顺手告知密码」而加一个
    password 参数，这条断言会立刻失败。
    """
    import inspect

    from app.services.email_delivery_service import build_review_mail

    params = list(inspect.signature(build_review_mail).parameters)
    assert params == ["user_name", "approved", "sender_name"], (
        f"审核邮件文案函数的入参被改动了（{params}）：它必须拿不到任何凭据，"
        "否则正文就可能泄露口令"
    )


# --------------------------------------------------------------------------- #
# 任务 13：审核动作触发发信
# --------------------------------------------------------------------------- #

STATUS_URL = "/api/portal/management/users/{uid}/status"


async def _seed_with_status(user_name, email, status):
    """建一个指定状态的用户，返回 (id, user_name)。

    只发信是尽力而为，因此这里保留 id 供 PATCH 使用、user_name 供清理使用。
    """
    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="审核测试", status=status, email=email, db=s
        )
        uid = (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar_one()
        return uid, user_name


async def _seed_pending(user_name, email):
    return await _seed_with_status(user_name, email, USER_STATUS_PENDING_REVIEW)


async def _seed_enabled(user_name, email):
    return await _seed_with_status(user_name, email, USER_STATUS_ENABLED)


def _smtp_settings(mod, **overrides):
    kwargs = dict(host="h", port=25, user="", password="",
                  security="none", from_address="f@a.cn", sender_name="X")
    kwargs.update(overrides)
    return mod.SmtpSettings(**kwargs)


async def test_review_approval_sends_mail(client, admin_api_key, db_session, monkeypatch):
    from app.services import email_delivery_service as mod

    sent = {}

    async def _fake_settings(user_id=None, scope="user", db=None):
        return _smtp_settings(mod)

    def _fake_send(settings, to, subject, body):
        sent["to"] = to
        sent["subject"] = subject
        return True, "发送成功"

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _fake_settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_fake_send))

    user_name = f"zrev_{uuid.uuid4().hex[:8]}"
    email = f"rev_{uuid.uuid4().hex[:8]}@corp.example.com"
    try:
        uid, _ = await _seed_pending(user_name, email)
        resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                                  headers={"X-API-Key": admin_api_key})
        assert resp.status_code == 200, resp.text
        assert resp.json().get("email_sent") is True
        assert sent["to"] == [email]
    finally:
        await _drop_user(user_name)


async def test_review_does_not_send_when_service_disabled(
    client, admin_api_key, db_session, monkeypatch
):
    from app.services import email_delivery_service as mod

    async def _none(user_id=None, scope="user", db=None):
        return None

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _none)

    user_name = f"zrev_{uuid.uuid4().hex[:8]}"
    email = f"rev_{uuid.uuid4().hex[:8]}@corp.example.com"
    try:
        uid, _ = await _seed_pending(user_name, email)
        resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                                  headers={"X-API-Key": admin_api_key})
        assert resp.status_code == 200, "邮件服务关闭绝不能阻塞审核"
        assert resp.json().get("email_sent") is False
    finally:
        await _drop_user(user_name)


async def test_review_records_error_when_send_fails(client, admin_api_key, monkeypatch):
    from app.services import email_delivery_service as mod

    async def _settings(user_id=None, scope="user", db=None):
        return _smtp_settings(mod)

    def _fail(settings, to, subject, body):
        return False, "SMTPAuthenticationError: 认证失败"

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_fail))

    user_name = f"zrev_{uuid.uuid4().hex[:8]}"
    email = f"rev_{uuid.uuid4().hex[:8]}@corp.example.com"
    try:
        uid, _ = await _seed_pending(user_name, email)
        resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                                  headers={"X-API-Key": admin_api_key})
        assert resp.status_code == 200
        assert resp.json()["email_sent"] is False
        assert "认证失败" in resp.json()["email_error"]
    finally:
        await _drop_user(user_name)


async def test_review_skips_mail_when_user_has_no_email(
    client, admin_api_key, monkeypatch
):
    from app.services import email_delivery_service as mod

    async def _settings(user_id=None, scope="user", db=None):
        return _smtp_settings(mod)

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)

    user_name = f"zrev_{uuid.uuid4().hex[:8]}"
    try:
        uid, _ = await _seed_pending(user_name, None)
        resp = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                                  headers={"X-API-Key": admin_api_key})
        assert resp.status_code == 200
        assert resp.json()["email_sent"] is False
        assert "邮箱" in resp.json()["email_error"]
    finally:
        await _drop_user(user_name)


async def test_review_does_not_mail_on_daily_enable_disable(
    client, admin_api_key, monkeypatch
):
    """原状态不是待审核的启停属于日常运维，发信会变成骚扰。"""
    from app.services import email_delivery_service as mod

    called = {"n": 0}

    async def _settings(user_id=None, scope="user", db=None):
        return _smtp_settings(mod)

    def _send(settings, to, subject, body):
        called["n"] += 1
        return True, "ok"

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_send))

    user_name = f"zdaily_{uuid.uuid4().hex[:8]}"
    email = f"daily_{uuid.uuid4().hex[:8]}@corp.example.com"
    try:
        uid, _ = await _seed_enabled(user_name, email)
        # 必须断言请求确实成功了：若 PATCH 返回 4xx/5xx，called 天然是 0，
        # 这条「日常启停不得发信」的唯一守卫就会空过。
        r1 = await client.patch(STATUS_URL.format(uid=uid), json={"status": 0},
                                headers={"X-API-Key": admin_api_key})
        r2 = await client.patch(STATUS_URL.format(uid=uid), json={"status": 1},
                                headers={"X-API-Key": admin_api_key})
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert called["n"] == 0, "非审核动作不得发信"
    finally:
        await _drop_user(user_name)


# --------------------------------------------------------------------------- #
# 任务 14：重发端点
# --------------------------------------------------------------------------- #

RESEND_URL = "/api/portal/management/users/{uid}/notify-review-mail"


async def test_resend_review_mail(client, admin_api_key, monkeypatch):
    from app.services import email_delivery_service as mod

    captured = {}

    async def _settings(user_id=None, scope="user", db=None):
        return _smtp_settings(mod)

    def _send(settings, to, subject, body):
        captured["subject"] = subject
        return True, "ok"

    monkeypatch.setattr(mod.EmailDeliveryService, "load_global_smtp_settings", _settings)
    monkeypatch.setattr(mod.EmailDeliveryService, "send_mail", staticmethod(_send))

    user_name = f"zres_{uuid.uuid4().hex[:8]}"
    email = f"res_{uuid.uuid4().hex[:8]}@corp.example.com"
    try:
        uid, _ = await _seed_enabled(user_name, email)
        resp = await client.post(RESEND_URL.format(uid=uid), json={"approved": True},
                                 headers={"X-API-Key": admin_api_key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert "启用" in captured["subject"]
    finally:
        await _drop_user(user_name)


async def test_resend_rejected_for_pending_user(client, admin_api_key):
    user_name = f"zres_{uuid.uuid4().hex[:8]}"
    try:
        uid, _ = await _seed_pending(user_name, "p@a.cn")
        resp = await client.post(RESEND_URL.format(uid=uid), json={"approved": True},
                                 headers={"X-API-Key": admin_api_key})
        assert resp.status_code == 400
    finally:
        await _drop_user(user_name)


# --------------------------------------------------------------------------- #
# 任务 16：个人通知渠道分层 + 抄送自己
# --------------------------------------------------------------------------- #

import json as _json


@pytest.mark.no_infrastructure
def test_email_default_config_has_source_and_include_self():
    from app.services.notification_service import NotificationService
    cfg = NotificationService.DEFAULT_CONFIGS["email"]
    assert cfg["smtp_source"] == "global", "默认使用全局邮件服务"
    assert cfg["include_self"] is True, "默认抄送自己"


async def _seed_email_channel(user_name, email, config):
    """建用户 + 写入其 email 通知配置，返回 user_id。"""
    from app.models.user_notification_config import UserNotificationConfig

    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="通知分层测试", email=email, db=s
        )
        uid = (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar_one()
        s.add(
            UserNotificationConfig(
                user_id=uid,
                channel_type="email",
                config_json=_json.dumps(config, ensure_ascii=False),
            )
        )
        await s.commit()
        return uid


async def _drop_notify_user(user_name):
    """清理用户及其通知配置，避免共享库残留。"""
    from app.models.user_notification_config import UserNotificationConfig

    async with AsyncSessionLocal() as s:
        uid = (await s.execute(select(User.id).where(User.user_name == user_name))).scalar()
        if uid is not None:
            await s.execute(
                delete(UserNotificationConfig).where(UserNotificationConfig.user_id == uid)
            )
            await s.execute(delete(UserRoleRelation).where(UserRoleRelation.user_id == uid))
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()


def _patch_email_send(monkeypatch, captured):
    """把分层解析与真实发送都替换掉，只观察收件人与是否发信。"""
    from app.services import email_delivery_service as edmod

    async def _settings(user_id=None, scope="user", db=None):
        return _smtp_settings(edmod)

    def _send(settings, to, subject, body, **kwargs):
        # **kwargs 不能省：send_email 会传 raise_on_transport_error=True。
        # 替身签名必须跟进真实签名，否则 TypeError 会被 _send_with_retries 当成
        # 可重试的传输故障，白白退避重试两轮（实测让这套用例从 3 秒涨到 41 秒）。
        captured["kwargs"] = kwargs
        captured["to"] = list(to)
        captured["calls"] = captured.get("calls", 0) + 1
        return True, "ok"

    monkeypatch.setattr(edmod.EmailDeliveryService, "resolve_smtp_settings", _settings)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))
    return edmod


async def test_send_email_includes_self_email_and_extra(monkeypatch):
    """include_self 缺失视为 True（新老用户一致），并与其他收件人合并。"""
    from app.services.notification_service import NotificationService

    captured = {}
    _patch_email_send(monkeypatch, captured)

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        uid = await _seed_email_channel(
            name, email, {"is_enabled": True, "recipients": "other@x.com"}
        )
        async with AsyncSessionLocal() as s:
            ok, msg = await NotificationService.send_email(s, uid, "标题", "正文")
        assert ok is True, msg
        assert captured["to"] == [email, "other@x.com"]
    finally:
        await _drop_notify_user(name)


async def test_send_email_dedupes_self_already_in_recipients(monkeypatch):
    """存量用户 recipients 里可能已写了自己，不能收两封。"""
    from app.services.notification_service import NotificationService

    captured = {}
    _patch_email_send(monkeypatch, captured)

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        uid = await _seed_email_channel(
            name, email, {"is_enabled": True, "include_self": True,
                          "recipients": f"{email}, other@x.com"}
        )
        async with AsyncSessionLocal() as s:
            ok, _ = await NotificationService.send_email(s, uid, "标题", "正文")
        assert ok is True
        assert captured["to"] == [email, "other@x.com"]
    finally:
        await _drop_notify_user(name)


async def test_send_email_follows_updated_email(monkeypatch):
    """include_self 存的是标志而非邮箱快照：用户改邮箱后收件人自动跟随。"""
    from app.services.notification_service import NotificationService

    captured = {}
    _patch_email_send(monkeypatch, captured)

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    first = f"{name}_a@corp.example.com"
    second = f"{name}_b@corp.example.com"
    try:
        uid = await _seed_email_channel(name, first, {"is_enabled": True})
        async with AsyncSessionLocal() as s:
            await NotificationService.send_email(s, uid, "标题", "正文")
        assert captured["to"] == [first]

        async with AsyncSessionLocal() as s:
            await s.execute(
                User.__table__.update().where(User.id == uid).values(email=second)
            )
            await s.commit()

        async with AsyncSessionLocal() as s:
            await NotificationService.send_email(s, uid, "标题", "正文")
        assert captured["to"] == [second], "写入的必须是标志，收件人应随邮箱变化"
    finally:
        await _drop_notify_user(name)


async def test_send_email_global_source_without_recipients_fails(monkeypatch):
    """全局模式下没有收件人时不能回退发给发件账号——那是静默的错误投递。"""
    from app.services.notification_service import NotificationService

    captured = {}
    _patch_email_send(monkeypatch, captured)

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    try:
        uid = await _seed_email_channel(
            name, None, {"is_enabled": True, "smtp_source": "global", "recipients": ""}
        )
        async with AsyncSessionLocal() as s:
            ok, msg = await NotificationService.send_email(s, uid, "标题", "正文")
        assert ok is False, "全局模式不得回退发件人"
        assert "收件人" in msg
        assert captured.get("calls", 0) == 0, "没有收件人时不得真正发信"
    finally:
        await _drop_notify_user(name)


async def test_send_email_custom_source_falls_back_to_sender(monkeypatch):
    """自定义 SMTP 下保留既有行为：没有收件人时发给发件账号自身。"""
    from app.services import email_delivery_service as edmod
    from app.services.notification_service import NotificationService

    captured = {}
    _patch_email_send(monkeypatch, captured)

    async def _settings(user_id=None, scope="user", db=None):
        # from_address 留空，让 effective_from() 落回 SMTP 登录账号——这才对应
        # 升级前「recipients 为空则发给 smtp_user」的行为。
        return _smtp_settings(edmod, user="sender@x.com", from_address="")

    monkeypatch.setattr(edmod.EmailDeliveryService, "resolve_smtp_settings", _settings)

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    try:
        uid = await _seed_email_channel(
            name, None,
            {"is_enabled": True, "smtp_source": "custom", "smtp_host": "smtp.x.com",
             "smtp_user": "sender@x.com", "recipients": ""},
        )
        async with AsyncSessionLocal() as s:
            ok, msg = await NotificationService.send_email(s, uid, "标题", "正文")
        assert ok is True, msg
        assert captured["to"] == ["sender@x.com"]
    finally:
        await _drop_notify_user(name)


# --------------------------------------------------------------------------- #
# 传输层失败必须可重试（回归守卫）
#
# 背景：send_mail 默认吞掉所有异常并返回 (False, msg)，而
# _send_with_retries 的契约是「抛异常 = 瞬时故障，退避重试；返回 (False, err)
# = 业务失败，立即返回」。如果连接失败也走返回值，邮件渠道的退避重试就静默失效
# ——这是本次改造真实引入过的回归，所以两条链路都要钉住。
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
def test_send_mail_transport_error_is_raisable_for_retry():
    from app.services.email_delivery_service import (
        EmailDeliveryService,
        SmtpDeliveryError,
        SmtpSettings,
    )

    # 127.0.0.1:1 必然连接被拒，且不依赖外网，测试是自洽的
    settings = SmtpSettings(
        host="127.0.0.1", port=1, user="", password="",
        security="none", from_address="f@a.cn", sender_name="X",
    )

    # 默认行为：不抛异常，把原因交给调用方展示（测试发送按钮/审核通知要用）
    ok, message = EmailDeliveryService.send_mail(settings, ["t@a.cn"], "s", "b")
    assert ok is False
    assert message, "失败必须带上可展示的原因"

    # 重试场景：抛出来，让 _send_with_retries 能识别为瞬时故障
    with pytest.raises(SmtpDeliveryError):
        EmailDeliveryService.send_mail(
            settings, ["t@a.cn"], "s", "b", raise_on_transport_error=True
        )


@pytest.mark.no_infrastructure
def test_raised_transport_error_is_password_sanitized():
    """抛出的异常会被记进日志与 last_err，必须先把口令抹掉。"""
    from app.services.email_delivery_service import (
        EmailDeliveryService,
        SmtpDeliveryError,
        SmtpSettings,
    )

    settings = SmtpSettings(
        host="127.0.0.1", port=1, user="u", password="Sup3rSecret!",
        security="none", from_address="f@a.cn", sender_name="X",
    )
    with pytest.raises(SmtpDeliveryError) as excinfo:
        EmailDeliveryService.send_mail(
            settings, ["t@a.cn"], "s", "b", raise_on_transport_error=True
        )
    assert "Sup3rSecret" not in str(excinfo.value)


@pytest.mark.no_infrastructure
def test_send_mail_business_failure_does_not_raise_even_when_retryable_requested():
    """业务性失败不该伪装成可重试：配置不完整/无收件人只能靠返回值。"""
    from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings

    incomplete = SmtpSettings(
        host="", port=25, user="", password="",
        security="none", from_address="", sender_name="X",
    )
    ok, message = EmailDeliveryService.send_mail(
        incomplete, ["t@a.cn"], "s", "b", raise_on_transport_error=True
    )
    assert ok is False and "配置不完整" in message

    # 收件人为空要单独用**完整**配置来测：配置不完整的检查在前，会先把请求
    # 短路掉，拿不到「未配置收件人」这条分支。
    complete = SmtpSettings(
        host="smtp.a.cn", port=25, user="", password="",
        security="none", from_address="f@a.cn", sender_name="X",
    )
    ok2, message2 = EmailDeliveryService.send_mail(
        complete, [], "s", "b", raise_on_transport_error=True
    )
    assert ok2 is False and "收件人" in message2


async def test_send_with_retries_retries_transport_failures(monkeypatch):
    """钉住重试机制本身：前两次抛异常、第三次成功 → 应重试到成功。"""
    from app.services import notification_service as ns

    monkeypatch.setattr(ns, "_SEND_RETRY_DELAYS_SEC", (0, 0))
    calls = {"n": 0}

    async def _send_once():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("connection reset")
        return True, ""

    ok, err = await ns._send_with_retries(_send_once, channel="email")
    assert ok is True, err
    assert calls["n"] == 3, "瞬时故障必须被重试"


async def test_send_with_retries_does_not_retry_business_failures(monkeypatch):
    """业务失败立即返回，不做无谓重试。"""
    from app.services import notification_service as ns

    monkeypatch.setattr(ns, "_SEND_RETRY_DELAYS_SEC", (0, 0))
    calls = {"n": 0}

    async def _send_once():
        calls["n"] += 1
        return False, "未配置收件人"

    ok, err = await ns._send_with_retries(_send_once, channel="email")
    assert ok is False and err == "未配置收件人"
    assert calls["n"] == 1, "业务失败不该重试"


async def test_send_email_marks_transport_failures_retryable(monkeypatch):
    """**集成守卫**：邮件渠道必须把传输层失败交给退避重试，而不是当业务失败吞掉。

    背景：send_mail 默认吞掉所有异常并返回 (False, msg)，而 _send_with_retries 的
    契约是「抛异常 = 瞬时故障 → 重试；返回 (False, err) = 业务失败 → 立即返回」。
    改造 send_email 时若忘了传 raise_on_transport_error=True，连接超时就会被
    当成业务失败立即返回，邮件的退避重试静默失效（钉钉/企微仍会重试，只有邮件退化）。

    这条用例同时断言「传了可重试标志」与「确实重试了」，只测其中一项都守不住。
    """
    from app.services import email_delivery_service as edmod
    from app.services.notification_service import NotificationService

    calls = {"n": 0, "kwargs": None}

    async def _settings(user_id=None, scope="user", db=None):
        return _smtp_settings(edmod)

    def _send(settings, to, subject, body, **kwargs):
        calls["n"] += 1
        calls["kwargs"] = kwargs
        raise edmod.SmtpDeliveryError("SMTPConnectError: 连接被拒绝")

    monkeypatch.setattr(edmod.EmailDeliveryService, "resolve_smtp_settings", _settings)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))
    # 把退避延迟归零，测试不必真的等
    monkeypatch.setattr("app.services.notification_service._SEND_RETRY_DELAYS_SEC", (0, 0))

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        uid = await _seed_email_channel(name, email, {"is_enabled": True})
        async with AsyncSessionLocal() as s:
            ok, msg = await NotificationService.send_email(s, uid, "标题", "正文")
        assert ok is False and msg, "瞬时故障最终仍是失败"
        assert calls["kwargs"].get("raise_on_transport_error") is True, (
            "必须传 raise_on_transport_error=True，否则瞬时故障会被当成业务失败而不重试"
        )
        assert calls["n"] == 3, (
            f"应重试到次数用尽（len(_SEND_RETRY_DELAYS_SEC)+1 = 3），实际只调用了 {calls['n']} 次"
        )
    finally:
        await _drop_notify_user(name)


async def test_send_email_business_failure_is_not_retried(monkeypatch):
    """业务失败（无收件人）不该被重试三次。"""
    from app.services.notification_service import NotificationService

    captured = {}
    _patch_email_send(monkeypatch, captured)
    monkeypatch.setattr("app.services.notification_service._SEND_RETRY_DELAYS_SEC", (0, 0))

    name = f"zmail_{uuid.uuid4().hex[:8]}"
    try:
        # 全局来源 + 不抄送自己 + 无其它收件人 → 必然「未配置收件人」
        uid = await _seed_email_channel(
            name, None, {"is_enabled": True, "smtp_source": "global",
                         "include_self": False, "recipients": ""}
        )
        async with AsyncSessionLocal() as s:
            ok, msg = await NotificationService.send_email(s, uid, "标题", "正文")
        assert ok is False and "收件人" in msg
        assert captured.get("calls", 0) <= 1, "业务失败不该重试"
    finally:
        await _drop_notify_user(name)


# --------------------------------------------------------------------------- #
# 个人中心「邮件消息 › 测试连通性」必须遵守全局/自定义分层
#
# 缺陷：_test_email 是第三份独立 SMTP 实现，完全不知道 smtp_source 分层，无条件
# 要求表单里有 host/账号/密码。而前端在选「全局」（含全部存量用户与新用户默认值）
# 时根本不渲染这三个输入框，提交上来是空串 → 必然 400「SMTP 服务地址、账号和授权
# 码不能为空」，把「管理员没开全局服务」的责任推给用户去填一堆界面没显示的字段。
# 该路径默认值 100% 不可用，且既有测试全部 mock 掉 _test_* 从不覆盖真实实现。
# --------------------------------------------------------------------------- #

def _email_test_payload(source: str, **overrides):
    """复刻前端 configs.email 的完整结构（未渲染的字段以空串提交）。"""
    cfg = {
        "is_enabled": True,
        "smtp_source": source,
        "include_self": True,
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_user": "",
        "smtp_password": "",
        "sender_name": "AI Agent",
        "recipients": "",
    }
    cfg.update(overrides)
    return {"channel_type": "email", "config_data": cfg}


async def test_email_test_global_source_reports_global_service_state(
    client, valid_api_key, monkeypatch
):
    """选「全局」而平台服务未开时，提示必须指向平台服务，而不是怪用户没填隐藏字段。"""
    from app.services import email_delivery_service as edmod

    async def _global_settings():
        return None  # 平台邮件服务未启用

    monkeypatch.setattr(
        edmod.EmailDeliveryService, "load_global_smtp_settings", _global_settings
    )

    resp = await client.post(
        "/api/portal/notifications/test",
        json=_email_test_payload("global"),
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail") or resp.json().get("message") or ""
    assert "SMTP 服务地址、账号和授权码不能为空" not in detail, (
        "选全局时前端不渲染这些字段，不能把责任推给用户：%r" % detail
    )
    assert "平台邮件服务" in detail, f"应指明是平台（全局）邮件服务的问题: {detail!r}"


async def test_email_test_global_source_uses_global_settings(
    client, valid_api_key, monkeypatch
):
    """选「全局」时必须用平台配置发测试信，而不是表单里的空值。"""
    from app.services import email_delivery_service as edmod

    settings = edmod.SmtpSettings(
        host="global.smtp.example.com", port=465, user="g@a.cn", password="pw",
        security="ssl", from_address="g@a.cn", sender_name="平台",
    )
    captured = {}

    async def _global_settings():
        return settings

    async def _self_email(user_id, db):
        return "self@corp.example.com"

    def _send(smtp, to, subject, body, **kwargs):
        captured["host"] = smtp.host
        captured["to"] = list(to)
        return True, ""

    monkeypatch.setattr(
        edmod.EmailDeliveryService, "load_global_smtp_settings", _global_settings
    )
    monkeypatch.setattr(edmod.EmailDeliveryService, "resolve_self_email", _self_email)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))

    resp = await client.post(
        "/api/portal/notifications/test",
        json=_email_test_payload("global"),
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200, resp.json()
    assert captured["host"] == "global.smtp.example.com"
    assert captured["to"] == ["self@corp.example.com"]


async def test_email_test_only_sends_to_self_even_if_recipients_configured(
    client, valid_api_key, monkeypatch
):
    """连通性测试**只能发给自己**，即使配置里写了其他收件人。

    这是「借平台全局 SMTP 向任意地址发信」的守卫：本接口只要求登录、没有频控，
    若收件人可由请求体/配置决定，任意登录用户就能把平台当成对外发信中继。
    """
    from app.services import email_delivery_service as edmod

    settings = edmod.SmtpSettings(
        host="global.smtp.example.com", port=465, user="g@a.cn", password="pw",
        security="ssl", from_address="g@a.cn", sender_name="平台",
    )
    captured = {}

    async def _global_settings():
        return settings

    async def _self_email(user_id, db):
        return "self@corp.example.com"

    def _send(smtp, to, subject, body, **kwargs):
        captured["to"] = list(to)
        return True, ""

    monkeypatch.setattr(
        edmod.EmailDeliveryService, "load_global_smtp_settings", _global_settings
    )
    monkeypatch.setattr(edmod.EmailDeliveryService, "resolve_self_email", _self_email)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))

    resp = await client.post(
        "/api/portal/notifications/test",
        json=_email_test_payload(
            "global",
            recipients="victim@external.example.com, another@external.example.com",
        ),
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200, resp.json()
    assert captured["to"] == ["self@corp.example.com"], (
        "配置里的其他收件人必须被忽略，测试只能发给自己"
    )
    assert "victim@external.example.com" not in captured["to"]


async def test_email_test_custom_source_uses_form_settings(
    client, valid_api_key, monkeypatch
):
    """选「自定义」时必须用表单里的配置发测试信。"""
    from app.services import email_delivery_service as edmod

    captured = {}

    def _send(smtp, to, subject, body, **kwargs):
        captured["host"] = smtp.host
        captured["security"] = smtp.security
        captured["to"] = list(to)
        return True, ""

    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))

    resp = await client.post(
        "/api/portal/notifications/test",
        json=_email_test_payload(
            "custom", smtp_host="my.smtp.cn", smtp_user="me@a.cn",
            smtp_password="pw", smtp_port=587, security="starttls",
        ),
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 200, resp.json()
    assert captured["host"] == "my.smtp.cn"
    assert captured["security"] == "starttls", "必须带上用户选择的加密方式"


async def test_email_test_custom_source_without_host_is_rejected(
    client, valid_api_key, monkeypatch
):
    """自定义但没填地址时给出准确的提示（内网中继可以没有账号密码）。"""
    from app.services import email_delivery_service as edmod

    def _send(*a, **k):
        raise AssertionError("配置不完整时不该尝试发送")

    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))

    resp = await client.post(
        "/api/portal/notifications/test",
        json=_email_test_payload("custom", smtp_host=""),
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 400
    detail = resp.json().get("detail", "")
    assert "SMTP 服务地址" in detail


async def test_email_test_global_source_without_any_recipient_says_so(
    client, valid_api_key, monkeypatch
):
    """本人没有邮箱时要明确报错，绝不回退发给平台/发件账号。

    用 monkeypatch 把「自己邮箱」打成 None，而不是去改共享库里 fixture 用户的邮箱：
    改真实数据既可能破坏它，也让用例依赖库的当前状态。
    """
    from app.services import email_delivery_service as edmod

    settings = edmod.SmtpSettings(
        host="g.smtp.cn", port=465, user="g@a.cn", password="pw",
        security="ssl", from_address="g@a.cn", sender_name="平台",
    )

    async def _global_settings():
        return settings

    async def _no_self_email(user_id, db):
        return None

    def _send(*a, **k):
        # 这正是要守住的点：不能因为「发件账号有地址」就偷偷发给自己，
        # 否则用户看到「测试通过」却永远收不到真实通知。
        raise AssertionError("全局模式且无收件人时不该尝试发送")

    monkeypatch.setattr(
        edmod.EmailDeliveryService, "load_global_smtp_settings", _global_settings
    )
    monkeypatch.setattr(edmod.EmailDeliveryService, "resolve_self_email", _no_self_email)
    monkeypatch.setattr(edmod.EmailDeliveryService, "send_mail", staticmethod(_send))

    resp = await client.post(
        "/api/portal/notifications/test",
        # 即便配置里写了收件人也必须拒绝：测试只发给自己，而本人没有邮箱
        json=_email_test_payload("global", recipients="other@x.com"),
        headers={"X-API-Key": valid_api_key},
    )
    assert resp.status_code == 400
    assert "收件人" in resp.json().get("detail", "")


def test_test_email_no_longer_hand_rolls_smtp():
    """**结构性守卫**：_test_email 不得再自建 SMTP 连接。

    这个缺陷的形态就是「又多出一份实现」，逐个断言分支行为挡不住第四份拷贝；
    直接钉住「代码里不出现 smtplib / starttls」，从来源上禁止再抄一份。
    断言前先剥掉 docstring：说明文字里提到这些词是应该的，只有可执行代码不能有。
    """
    import ast
    import inspect
    import textwrap
    from app.services.notification_service import NotificationService

    tree = ast.parse(textwrap.dedent(inspect.getsource(NotificationService._test_email)))
    fn = tree.body[0]
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # 去掉 docstring
    code = "\n".join(ast.unparse(node) for node in body)

    assert "smtplib" not in code, "必须复用 EmailDeliveryService.send_mail，不要自己连 SMTP"
    assert "starttls" not in code, "加密方式由 EmailDeliveryService 统一处理，且不得静默降级"
    assert "send_mail" in code, "应当委托给统一的发送实现"


# --------------------------------------------------------------------------- #
# 代码评审补充的回归守卫
#
# 每条都对应一个真实修过的缺陷，且必须满足「去掉修复即失败」。
# --------------------------------------------------------------------------- #

@pytest.mark.no_infrastructure
def test_custom_smtp_security_inferred_from_port_when_absent():
    """自定义 SMTP 缺少加密方式时按端口推断（真实回归）。

    个人中心的自定义 SMTP 表单**没有**加密方式字段，配置里就不存在
    security/smtp_security，于是恒取兜底值。历史实现是按端口自适应的
    （465 → SSL，其余 → STARTTLS），若兜底成 'ssl'，所有 587（企业邮箱
    最常见的提交端口）/25 端口的存量用户会从「可用」变成「必然失败」。
    """
    from app.services.email_delivery_service import EmailDeliveryService as E

    base = {"smtp_host": "smtp.example.com", "smtp_user": "u@x.com", "smtp_password": "p"}

    assert E.build_custom_settings({**base, "smtp_port": 465}).security == "ssl"
    assert E.build_custom_settings({**base, "smtp_port": 587}).security == "starttls"
    assert E.build_custom_settings({**base, "smtp_port": 25}).security == "starttls"
    assert E.build_custom_settings(base).security == "ssl"  # 端口缺省即 465

    # 显式值始终优先（全局配置路径可填 starttls / none）
    assert E.build_custom_settings({**base, "smtp_port": 587, "smtp_security": "none"}).security == "none"
    assert E.build_custom_settings({**base, "smtp_port": 465, "security": "starttls"}).security == "starttls"

    # 非法值必须报错，不能静默接受
    with pytest.raises(ValueError):
        E.build_custom_settings({**base, "smtp_security": "tls"})
    with pytest.raises(ValueError):
        E.build_custom_settings({**base, "smtp_port": "not-a-port"})


@pytest.mark.no_infrastructure
def test_send_mail_rejects_unknown_security_without_connecting(monkeypatch):
    """未知加密方式必须直接报错，绝不能落到「非 ssl 即明文」。

    判定为 ssl/starttls 两分支时，脏值（手改库、经 API 直传的 'tls'）会让
    加密被静默跳过、口令明文发出——违反「绝不静默降级」。
    """
    from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings

    connected = []

    class _Boom:
        def __init__(self, *a, **k):
            connected.append(a)

    monkeypatch.setattr("smtplib.SMTP", _Boom)
    monkeypatch.setattr("smtplib.SMTP_SSL", _Boom)

    settings = SmtpSettings(
        host="smtp.example.com", port=587, user="u@x.com", password="secret",
        security="tls", from_address="u@x.com", sender_name="AI",
    )
    ok, message = EmailDeliveryService.send_mail(settings, ["to@x.com"], "s", "b")

    assert ok is False
    assert "加密方式" in message
    assert connected == [], "未知加密方式下不得建立任何连接，否则就是明文发送"


@pytest.mark.no_infrastructure
def test_starttls_failure_does_not_fall_back_to_plaintext(monkeypatch):
    """starttls 失败必须失败，且绝不能继续 login/sendmail（行为级验证）。

    既有断言只扫源码字符串或只看传入的 settings.security，
    把实现改回 `try: starttls() except: pass` 也不会失败——那种静默降级
    会让 587 端口在服务器不支持 STARTTLS 时明文发送凭据。
    """
    from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings

    calls = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, *args, **kwargs):
            calls.append("starttls")
            raise RuntimeError("STARTTLS not supported by server")

        def login(self, *args, **kwargs):
            calls.append("login")

        def sendmail(self, *args, **kwargs):
            calls.append("sendmail")

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)

    settings = SmtpSettings(
        host="smtp.example.com", port=587, user="u@x.com", password="secret",
        security="starttls", from_address="u@x.com", sender_name="AI",
    )
    ok, _message = EmailDeliveryService.send_mail(settings, ["to@x.com"], "s", "b")

    assert ok is False
    assert "starttls" in calls, "应当尝试过 STARTTLS"
    assert "login" not in calls, "STARTTLS 失败后绝不能明文登录"
    assert "sendmail" not in calls, "STARTTLS 失败后绝不能明文发信"


@pytest.mark.no_infrastructure
def test_send_mail_scrubs_password_from_transport_error(monkeypatch):
    """异常文本里带了口令时，返回给调用方的 message 必须已脱敏。

    只测「连不上的主机」是假绿：那种异常文本本来就不含口令，
    把 sanitize_error_text 整个删掉断言依然成立。
    """
    from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings

    password = "SuperSecretAuthCode123"

    class _LeakySMTP:
        def __init__(self, host, port, timeout=None):
            raise RuntimeError(
                f"535 auth failed for u@x.com with password {password}"
            )

    monkeypatch.setattr("smtplib.SMTP_SSL", _LeakySMTP)

    settings = SmtpSettings(
        host="smtp.example.com", port=465, user="u@x.com", password=password,
        security="ssl", from_address="u@x.com", sender_name="AI",
    )
    ok, message = EmailDeliveryService.send_mail(settings, ["to@x.com"], "s", "b")

    assert ok is False
    assert password not in message, "异常文本里的口令必须被抹掉"
    assert EmailDeliveryService.MASK in message


@pytest.mark.no_infrastructure
def test_parse_recipients_handles_fullwidth_separators():
    """中文输入法下的全角逗号/分号必须能正确分隔（真实回归）。

    旧实现做了 `，`/`；` 归一，重写时丢失：`a@x.com，b@y.com` 会被当成
    **一个**收件人塞进 sendmail，导致整条通知发送失败。
    """
    from app.services.email_delivery_service import EmailDeliveryService

    assert EmailDeliveryService.parse_recipients("a@x.com，b@y.com；c@z.com") == [
        "a@x.com", "b@y.com", "c@z.com",
    ]
    assert EmailDeliveryService.parse_recipients("a@x.com, a@x.com") == ["a@x.com"]
    assert EmailDeliveryService.parse_recipients("not-an-email, ,;") == []


@pytest.mark.no_infrastructure
def test_is_email_unique_violation_matches_both_dialects():
    """邮箱唯一索引的识别——「最高风险分支」的判定函数本体。

    该函数此前全仓无任何引用，等于无人守卫。
    """
    from sqlalchemy.exc import IntegrityError as SAIntegrityError

    class _Orig(Exception):
        pass

    def _make(message):
        return SAIntegrityError("stmt", {}, _Orig(message))

    # MySQL 8.0
    assert AuthService.is_email_unique_violation(_make(
        "Duplicate entry 'a@b.com' for key 'ai_agent_users.uk_ai_agent_users_email'"
    )) is True
    # PostgreSQL
    assert AuthService.is_email_unique_violation(_make(
        'duplicate key value violates unique constraint "uk_ai_agent_users_email"'
    )) is True
    # 账号名冲突绝不能被误判成邮箱冲突
    assert AuthService.is_email_unique_violation(_make(
        "Duplicate entry 'x' for key 'ai_agent_users.uk_ai_agent_users_user_name'"
    )) is False


async def test_register_email_conflict_via_index_still_reports_email(
    client, monkeypatch, registration_on, cleanup_registered
):
    """预检没拦住、由唯一索引兜底时，必须仍报「邮箱」而不是「账号名」。

    这条用例存在的意义：预检命中时请求根本走不到 IntegrityError 分支，
    所以只测「重复邮箱」无法覆盖 is_email_unique_violation——把那个判定
    删掉（或让它恒返回 False），纯预检的用例依然全绿（假绿）。
    这里先把预检打成「永远说没被占用」，模拟并发抢注。
    """
    email = f"race_{uuid.uuid4().hex[:8]}@example.com"
    first = f"zmail_{uuid.uuid4().hex[:8]}"
    second = f"zmail_{uuid.uuid4().hex[:8]}"
    cleanup_registered.extend([first, second])

    assert (await client.post(REGISTER_URL, json=_payload(first, email))).status_code == 200

    async def _never_taken(*args, **kwargs):
        return False

    monkeypatch.setattr(AuthService, "is_user_email_taken", _never_taken)

    resp = await client.post(REGISTER_URL, json=_payload(second, email))
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "邮箱" in detail, "唯一索引兜底时也必须报邮箱冲突"
    assert "账号名" not in detail, "绝不能误报成账号名冲突，否则用户会改错字段"


async def _email_of(uid):
    """直接查库读邮箱：接口层可能做兜底，行为守卫必须看落库的真实值。"""
    async with AsyncSessionLocal() as session:
        return (await session.execute(select(User.email).where(User.id == uid))).scalar()


async def test_admin_edit_absent_email_keeps_existing(client, admin_api_key):
    """PUT 不带 email 字段 = 不修改。

    判定若写成 `if email:`（而不是「字段是否出现在请求里」），
    下面「空串=清空」那条会静默失效，而这条仍然通过——所以两条都要有。
    """
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        r = await client.post(USERS_URL, json={"user_name": name, "email": email},
                              headers={"X-API-Key": admin_api_key})
        uid = r.json().get("id") or r.json().get("user_id")
        upd = await client.put(f"{USERS_URL}/{uid}", json={"real_name": "只改名"},
                               headers={"X-API-Key": admin_api_key})
        assert upd.status_code == 200, upd.text
        assert await _email_of(uid) == email, "缺省 email 不得改动已有邮箱"
    finally:
        await _cleanup(await _uid_of(name))


async def test_admin_edit_blank_email_clears_it(client, admin_api_key):
    """PUT 传空串 = 清空邮箱。

    管理员必须能清掉填错的邮箱；用 `if email:` 判定会导致永远清不掉。
    """
    name = f"zma_{uuid.uuid4().hex[:8]}"
    email = f"{name}@corp.example.com"
    try:
        r = await client.post(USERS_URL, json={"user_name": name, "email": email},
                              headers={"X-API-Key": admin_api_key})
        uid = r.json().get("id") or r.json().get("user_id")
        upd = await client.put(f"{USERS_URL}/{uid}", json={"email": ""},
                               headers={"X-API-Key": admin_api_key})
        assert upd.status_code == 200, upd.text
        assert await _email_of(uid) is None, "空串必须把邮箱清成 NULL"
    finally:
        await _cleanup(await _uid_of(name))
