"""注册申请通知管理员：行为测试。

约定与邮件找回测试一致：``@pytest.mark.no_infrastructure`` 表示「不需要 DB 也不需要
Redis」，只用于纯逻辑用例；碰数据库的用例**不能**打这个标记 —— tests/conftest.py:37
会因此跳过数据库连接池的销毁与重建，导致 got Future attached to a different loop。
"""

import inspect
import uuid
from datetime import datetime

import pytest
from sqlalchemy import delete, select, update

from app.core.orm import AsyncSessionLocal
from app.models.user import USER_STATUS_PENDING_REVIEW, User
from app.services.auth_service import AuthService
from app.services.email_delivery_service import (
    EmailDeliveryService,
    build_admin_review_notice,
)


# --------------------------------------------------------------------------- #
# Task 1：正文构造（纯函数）
# --------------------------------------------------------------------------- #
@pytest.mark.no_infrastructure
def test_notice_body_contains_all_fields():
    subject, body = build_admin_review_notice(
        user_name="zhangsan",
        real_name="张三",
        email="zhangsan@corp.example.com",
        remark="市场部",
        registered_at=datetime(2026, 10, 1, 7, 39, 3),
        pending_count=3,
        sender_name="AI 智能体平台",
    )

    assert subject == "【AI 智能体平台】有待审核的账号申请"
    for expected in (
        "账号名：zhangsan",
        "用户姓名：张三",
        "邮箱：zhangsan@corp.example.com",
        "备注：市场部",
        "申请时间：2026-10-01 07:39:03",
        "当前待审核：3 条",
        "请到「用户管理 → 待审核」处理",
    ):
        assert expected in body, f"正文缺少：{expected}"


@pytest.mark.no_infrastructure
def test_notice_omits_blank_remark_line():
    """备注为空/纯空白时整行不渲染，避免出现空的「备注：」。"""
    for blank in (None, "", "   "):
        _, body = build_admin_review_notice(
            user_name="u", real_name="n", email="u@corp.example.com",
            remark=blank, registered_at=datetime(2026, 10, 1, 0, 0, 0),
            pending_count=0, sender_name="平台",
        )
        assert "备注：" not in body, f"remark={blank!r} 时不应渲染备注行"


@pytest.mark.no_infrastructure
def test_notice_marks_missing_registrant_email():
    """注册者未填邮箱时渲染「未填写」，而不是留一行空的「邮箱：」。"""
    _, body = build_admin_review_notice(
        user_name="u", real_name="n", email="   ", remark=None,
        registered_at=None, pending_count=1, sender_name="平台",
    )
    assert "邮箱：未填写" in body
    assert "邮箱：\n" not in body


# --------------------------------------------------------------------------- #
# 建号范式：照抄 tests/test_password_reset.py:95-118，避免另起一套
# --------------------------------------------------------------------------- #
def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _seed_user(user_name: str, *, role: str = "user", email=None, status: int = 1):
    """建一个指定角色/邮箱的临时用户，返回 uid。忽略 generate_api_key 返回的明文 key。"""
    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="管理员通知测试", status=status, email=email,
            role=role, db=s,
        )
        return (
            await s.execute(select(User.id).where(User.user_name == user_name))
        ).scalar_one()


async def _drop_user(user_name: str) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(delete(User).where(User.user_name == user_name))
        await s.commit()


@pytest.fixture
async def admin_recipients():
    """两个有效邮箱的管理员 + 一个无邮箱管理员 + 一个普通用户。

    注意这里**无法**构造「两个仅大小写不同的邮箱」：MySQL 的唯一索引落在 _ci collation 上，
    插入会被 uk_ai_agent_users_email 拒绝（PG 上则可以并存，见规范 §5）。去重逻辑因此
    抽成纯函数 dedupe_recipient_emails 单独测试。
    """
    suffix = uuid.uuid4().hex[:6]
    names = {
        "admin_a": _name("zadm_a"),
        "admin_b": _name("zadm_b"),
        "admin_no_mail": _name("zadm_nomail"),
        "plain": _name("zadm_plain"),
    }
    emails = {
        "admin_a": f"notify_{suffix}@corp.example.com",
        "admin_b": f"other_{suffix}@corp.example.com",
        "plain": f"plain_{suffix}@corp.example.com",
    }
    await _seed_user(names["admin_a"], role="admin", email=emails["admin_a"])
    await _seed_user(names["admin_b"], role="admin", email=emails["admin_b"])
    await _seed_user(names["admin_no_mail"], role="admin", email=None)
    await _seed_user(names["plain"], role="user", email=emails["plain"])
    try:
        yield names, emails
    finally:
        for name in names.values():
            await _drop_user(name)


# --------------------------------------------------------------------------- #
# Task 2a：收件人去重（纯函数）
# --------------------------------------------------------------------------- #
@pytest.mark.no_infrastructure
def test_dedupe_recipient_emails_dedupes_case_insensitively():
    """大小写不同的同一地址只留一条；None/空串/纯空白全部丢弃。

    这层去重存在的理由见规范 §5：PG 的唯一索引大小写敏感，且手工 SQL/第三方同步能绕过
    应用层归一化 —— 与 AuthService.is_user_email_taken 保持大小写不敏感比对同构。
    """
    from app.services.email_delivery_service import dedupe_recipient_emails

    rows = [
        "Notify@corp.example.com",
        "notify@CORP.EXAMPLE.COM",   # 与上一条仅大小写不同 → 必须去重
        None,
        "",
        "   ",
        "other@corp.example.com",
    ]
    assert dedupe_recipient_emails(rows) == [
        "Notify@corp.example.com",
        "other@corp.example.com",
    ]


# --------------------------------------------------------------------------- #
# Task 2b：收件人查询
# --------------------------------------------------------------------------- #
async def test_resolve_admin_recipients_filters_by_role_and_email(admin_recipients):
    names, emails = admin_recipients
    async with AsyncSessionLocal() as s:
        recipients = await EmailDeliveryService.resolve_admin_recipients(s)

    lowered = [r.lower() for r in recipients]
    assert emails["admin_a"].lower() in lowered
    assert emails["admin_b"].lower() in lowered
    assert emails["plain"].lower() not in lowered, "普通用户不该收到管理员通知"
    assert all(r.strip() for r in recipients), "收件人里不允许出现空串/纯空白"


async def test_resolve_admin_recipients_excludes_blank_email(admin_recipients):
    """空串邮箱的管理员必须被排除：一个坏地址会让整封邮件发不出去。

    应用层 normalize_email 会把空串转成 None，所以空串只可能来自手工 SQL 或第三方
    同步绕过应用层 —— 这里就用原始 SQL 造出这种数据。
    """
    names, emails = admin_recipients
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(User).where(User.user_name == names["admin_a"]).values(email="")
        )
        await s.commit()

    async with AsyncSessionLocal() as s:
        recipients = await EmailDeliveryService.resolve_admin_recipients(s)

    # 断言「空串本身没有进入收件人」——不能去断言它原来的地址不在列，那是恒真的
    # （值已经被改掉了），会让这条用例失去意义。
    assert recipients, "至少应还有一个有效管理员收件人"
    assert all(r.strip() for r in recipients), f"收件人里混入了空串/纯空白：{recipients!r}"
    assert "" not in recipients
    # 其它管理员不受影响
    assert emails["admin_b"].lower() in [r.lower() for r in recipients]


# --------------------------------------------------------------------------- #
# Task 3：编排（查收件人 → 取设置 → 构造 → 发送）
# --------------------------------------------------------------------------- #
class _FakeSmtpSettings:
    sender_name = "测试平台"


def _stub_global_smtp(monkeypatch, *, available: bool = True):
    """替换全局 SMTP 设置与 send_mail，返回调用记录。

    send_mail 的替身**必须是同步函数**：真实 send_mail 是同步 classmethod，调用方用
    run_in_threadpool 包装，async 替身会让 run_in_threadpool 返回 coroutine 对象，
    被当成真值 —— 桩静默失效、用例假绿。
    """
    calls: list = []

    async def _fake_load_global(cls=None):
        return _FakeSmtpSettings() if available else None

    def _fake_send_mail(cls, settings, to, subject, body, *, raise_on_transport_error=False):
        calls.append({"recipients": list(to), "subject": subject, "body": body})
        return True, None

    monkeypatch.setattr(
        EmailDeliveryService, "load_global_smtp_settings", classmethod(_fake_load_global)
    )
    monkeypatch.setattr(EmailDeliveryService, "send_mail", classmethod(_fake_send_mail))
    return calls


async def _empty():
    return []


async def test_notify_sends_to_all_admins(admin_recipients, monkeypatch):
    names, emails = admin_recipients
    registrant = _name("zadm_reg")
    uid = await _seed_user(
        registrant, role="user", email=f"{registrant}@corp.example.com", status=USER_STATUS_PENDING_REVIEW
    )
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert sent is True and error is None
        assert len(calls) == 1, "应当只发一封（收件人放同一个信封），而不是每个管理员各发一封"
        lowered = [r.lower() for r in calls[0]["recipients"]]
        assert emails["admin_a"].lower() in lowered
        assert emails["admin_b"].lower() in lowered
        assert emails["plain"].lower() not in lowered
        assert registrant in calls[0]["body"]
    finally:
        await _drop_user(registrant)


async def test_notify_skips_when_no_recipient(admin_recipients, monkeypatch):
    """没有任何可用收件人时：不发送、返回 (False, 原因)，且**不抛异常**。

    用真实存在的注册者，好让断言只指向「收件人守卫」这一处因果 —— 若用一个不存在的
    user_id，守卫被删掉后流程会走到「用户不存在」分支，用例可能因为别的原因仍然通过。
    """
    monkeypatch.setattr(
        EmailDeliveryService, "resolve_admin_recipients",
        classmethod(lambda cls, db: _empty()),
    )
    calls = _stub_global_smtp(monkeypatch, available=True)
    registrant = _name("zadm_norecip")
    uid = await _seed_user(registrant, role="user", email=None, status=USER_STATUS_PENDING_REVIEW)
    try:
        sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert sent is False
        assert error and "管理员" in error
        assert calls == []
    finally:
        await _drop_user(registrant)


async def test_notify_skips_when_global_mail_disabled(admin_recipients, monkeypatch):
    """全局邮件服务未启用：不发送，且返回的原因指向邮件服务。"""
    registrant = _name("zadm_disabled")
    uid = await _seed_user(registrant, role="user", email=None, status=USER_STATUS_PENDING_REVIEW)
    calls = _stub_global_smtp(monkeypatch, available=False)
    try:
        sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert sent is False
        assert error and "邮件服务" in error
        assert calls == []
    finally:
        await _drop_user(registrant)


async def test_notify_swallows_smtp_failure(admin_recipients, monkeypatch):
    """SMTP 抛异常也必须收敛成返回值，绝不冒泡到调用方（后台任务无人接收异常）。"""
    async def _fake_load_global(cls=None):
        return _FakeSmtpSettings()

    def _boom(cls, settings, to, subject, body, *, raise_on_transport_error=False):
        raise RuntimeError("SMTP 连接失败")

    monkeypatch.setattr(
        EmailDeliveryService, "load_global_smtp_settings", classmethod(_fake_load_global)
    )
    monkeypatch.setattr(EmailDeliveryService, "send_mail", classmethod(_boom))

    registrant = _name("zadm_boom")
    uid = await _seed_user(registrant, role="user", email=None, status=USER_STATUS_PENDING_REVIEW)
    try:
        sent, error = await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert sent is False and error is not None
    finally:
        await _drop_user(registrant)


async def test_pending_count_includes_the_new_registration(admin_recipients, monkeypatch):
    """计数必须与 /users/pending-count 同源，且包含刚提交的这条申请。"""
    calls = _stub_global_smtp(monkeypatch, available=True)
    registrant = _name("zadm_count")
    uid = await _seed_user(registrant, role="user", email=None, status=USER_STATUS_PENDING_REVIEW)
    try:
        async with AsyncSessionLocal() as s:
            expected = await AuthService.count_pending_registrations(db=s)
        await EmailDeliveryService.notify_admins_of_pending_registration(uid)
        assert expected >= 1
        assert f"当前待审核：{expected} 条" in calls[0]["body"]
    finally:
        await _drop_user(registrant)


# --------------------------------------------------------------------------- #
# Task 4：注册端点机制契约
# --------------------------------------------------------------------------- #
@pytest.mark.no_infrastructure
def test_register_endpoint_schedules_notice_as_background_task():
    """发信必须登记为 background task，而不是在响应前 await。

    为什么用源码契约而不是行为断言：TestClient 会等 BackgroundTasks 跑完才把响应交给
    测试，「快慢」在测试里观测不到；但「SMTP 超时拖慢注册接口」恰恰是要防的回归，
    所以只能对机制本身断言。
    """
    from app.api.portal.endpoints.auth import register

    source = inspect.getsource(register)
    assert "background_tasks.add_task(" in source
    assert "notify_admins_of_pending_registration" in source
    assert "await EmailDeliveryService.notify_admins_of_pending_registration" not in source
    # BackgroundTasks 必须是端点参数，否则 FastAPI 不会注入
    assert "background_tasks" in inspect.signature(register).parameters
    # 必须传 None（不能把请求作用域的 session 带进后台任务）
    assert "notify_admins_of_pending_registration, user_id, None" in source


# --------------------------------------------------------------------------- #
# Task 5：端到端 —— 注册响应体不得泄露内部通知行为
# --------------------------------------------------------------------------- #
REGISTER_URL = "/api/portal/auth/register"
VALID_PASSWORD = "Abcd1234!"  # 与 tests/test_user_registration.py 同款合规口令


def _reg_payload(name: str, *, email=None):
    return {
        "user_name": name,
        "real_name": "管理员通知测试",
        "email": email,
        "password": VALID_PASSWORD,
        "remark": "pytest 管理员通知用例",
    }


async def test_register_response_body_identical_with_and_without_mail(
    client, registration_on, monkeypatch, admin_recipients
):
    """邮件服务开/关两种情况下，注册响应体必须逐字节相同。

    否则等于对外泄露「管理员邮箱有没有配好」这类内部状态 —— 注册者不该从响应里
    推断出平台内部的收件人配置。
    """
    bodies = []
    names = []
    try:
        for available in (True, False):
            _stub_global_smtp(monkeypatch, available=available)
            name = _name("zadm_resp")
            names.append(name)
            resp = await client.post(REGISTER_URL, json=_reg_payload(name))
            assert resp.status_code == 200, resp.text
            bodies.append(resp.text)
    finally:
        for name in names:
            await _drop_user(name)

    assert bodies[0] == bodies[1], f"响应体泄露了内部通知行为：{bodies}"


async def test_register_still_succeeds_when_no_admin_has_email(
    client, registration_on, monkeypatch
):
    """一个可用收件人都没有时，注册仍必须成功（后台任务静默跳过）。"""
    monkeypatch.setattr(
        EmailDeliveryService, "resolve_admin_recipients",
        classmethod(lambda cls, db: _empty()),
    )
    calls = _stub_global_smtp(monkeypatch, available=True)
    name = _name("zadm_noadm")
    try:
        resp = await client.post(REGISTER_URL, json=_reg_payload(name, email=None))
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "success"
    finally:
        await _drop_user(name)
    assert calls == [], "没有收件人时不应该真的调用发信"
