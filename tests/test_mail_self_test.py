"""个人中心「测试发信」：行为测试。

需要 DB 与 Redis（冷却计数在 Redis），因此**不能**打 no_infrastructure 标记 ——
tests/conftest.py:37 会因此跳过数据库连接池的销毁与重建。
"""

import uuid

import pytest
from sqlalchemy import delete, select, update

from app.core import redis
from app.core.orm import AsyncSessionLocal
from app.models.user import USER_STATUS_ENABLED, User
from app.services.auth_service import AuthService
from app.services.email_delivery_service import EmailDeliveryService

ME_URL = "/api/portal/auth/me"
TEST_MAIL_URL = "/api/portal/auth/me/email-test"


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


async def _seed_user_with_key(user_name: str, *, email=None, role: str = "user"):
    """建用户并返回其明文 API Key（generate_api_key 内部落库并返回明文）。"""
    async with AsyncSessionLocal() as s:
        return await AuthService.generate_api_key(
            user_name, real_name="测试发信用例", status=USER_STATUS_ENABLED,
            email=email, role=role, db=s,
        )


async def _drop_user(user_name: str) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(delete(User).where(User.user_name == user_name))
        await s.commit()


class _FakeSmtpSettings:
    sender_name = "测试平台"


def _stub_global_smtp(monkeypatch, *, available: bool = True):
    """替换全局 SMTP 设置与 send_mail，返回调用记录。

    send_mail 的替身必须是**同步**函数：真实的是同步 classmethod，调用方用
    run_in_threadpool 包装，async 替身会让它返回 coroutine 对象并被当成真值。
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


@pytest.fixture
async def clear_mail_test_cooldown():
    """清掉本特性的冷却键，保证用例之间互不干扰。"""
    async def _purge():
        client = await redis.get_redis()
        if not client:
            return
        keys = [k async for k in client.scan_iter(match="auth:mailtest:*")]
        if keys:
            await client.delete(*keys)

    await _purge()
    yield
    await _purge()


# --------------------------------------------------------------------------- #
# Task A：/auth/me 的可用性标志位
# --------------------------------------------------------------------------- #
async def test_me_reports_mail_service_available(client, monkeypatch):
    name = _name("zmt_avail")
    key = await _seed_user_with_key(name, email=f"{name}@corp.example.com")
    try:
        _stub_global_smtp(monkeypatch, available=True)
        resp = await client.get(ME_URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["mail_service_available"] is True
    finally:
        await _drop_user(name)


async def test_me_reports_mail_service_unavailable(client, monkeypatch):
    name = _name("zmt_noavail")
    key = await _seed_user_with_key(name, email=f"{name}@corp.example.com")
    try:
        _stub_global_smtp(monkeypatch, available=False)
        resp = await client.get(ME_URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["mail_service_available"] is False
    finally:
        await _drop_user(name)


# --------------------------------------------------------------------------- #
# Task B：自测发信端点
# --------------------------------------------------------------------------- #
async def test_self_test_mail_goes_to_own_address(
    client, monkeypatch, clear_mail_test_cooldown
):
    name = _name("zmt_send")
    email = f"{name}@corp.example.com"
    key = await _seed_user_with_key(name, email=email)
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        resp = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True
        assert len(calls) == 1
        assert calls[0]["recipients"] == [email], "收件人必须是本人邮箱"
        assert name in calls[0]["body"]
        assert "邮箱可用性测试" in calls[0]["subject"]
    finally:
        await _drop_user(name)


async def test_self_test_mail_ignores_recipient_in_request_body(
    client, monkeypatch, clear_mail_test_cooldown
):
    """请求体里塞 to 也不能改变收件人 —— 否则任何登录用户都能拿它当发信中继。"""
    name = _name("zmt_relay")
    email = f"{name}@corp.example.com"
    key = await _seed_user_with_key(name, email=email)
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        resp = await client.post(
            TEST_MAIL_URL,
            headers={"X-API-Key": key},
            json={"to": "attacker@evil.example.com", "recipients": "attacker@evil.example.com"},
        )
        assert resp.status_code == 200, resp.text
        assert calls[0]["recipients"] == [email]
        assert "attacker" not in calls[0]["recipients"][0]
    finally:
        await _drop_user(name)


async def test_self_test_mail_rejected_without_email(
    client, monkeypatch, clear_mail_test_cooldown
):
    name = _name("zmt_nomail")
    key = await _seed_user_with_key(name, email=None)
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        resp = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert resp.status_code == 400, resp.text
        assert calls == []
    finally:
        await _drop_user(name)


async def test_self_test_mail_rejected_when_service_unavailable(
    client, monkeypatch, clear_mail_test_cooldown
):
    name = _name("zmt_off")
    key = await _seed_user_with_key(name, email=f"{name}@corp.example.com")
    calls = _stub_global_smtp(monkeypatch, available=False)
    try:
        resp = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert resp.status_code == 503, resp.text
        assert calls == []
    finally:
        await _drop_user(name)


async def test_self_test_mail_cooldown_blocks_second_call(
    client, monkeypatch, clear_mail_test_cooldown
):
    name = _name("zmt_cd")
    key = await _seed_user_with_key(name, email=f"{name}@corp.example.com")
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        first = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert first.status_code == 200 and first.json()["ok"] is True

        second = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert second.status_code == 429, second.text
        assert "秒" in second.json()["detail"], "冷却提示要告诉用户还要等多久"
        assert len(calls) == 1, "冷却期内不得真的再发一封"
    finally:
        await _drop_user(name)


async def test_cooldown_consumed_even_when_send_fails(
    client, monkeypatch, clear_mail_test_cooldown
):
    """发送失败也要占用冷却，否则 SMTP 配错时可以被无限猛刷。"""
    name = _name("zmt_cdfail")
    key = await _seed_user_with_key(name, email=f"{name}@corp.example.com")

    async def _fake_load_global(cls=None):
        return _FakeSmtpSettings()

    def _fail(cls, settings, to, subject, body, *, raise_on_transport_error=False):
        return False, "SMTP 认证失败"

    monkeypatch.setattr(
        EmailDeliveryService, "load_global_smtp_settings", classmethod(_fake_load_global)
    )
    monkeypatch.setattr(EmailDeliveryService, "send_mail", classmethod(_fail))
    try:
        first = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert first.status_code == 200 and first.json()["ok"] is False
        assert "SMTP 认证失败" in first.json()["message"]

        second = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert second.status_code == 429, "失败也必须占用冷却"
    finally:
        await _drop_user(name)


async def test_send_exception_does_not_become_500(
    client, monkeypatch, clear_mail_test_cooldown
):
    name = _name("zmt_boom")
    key = await _seed_user_with_key(name, email=f"{name}@corp.example.com")

    async def _fake_load_global(cls=None):
        return _FakeSmtpSettings()

    def _boom(cls, settings, to, subject, body, *, raise_on_transport_error=False):
        raise RuntimeError("连接被拒绝")

    monkeypatch.setattr(
        EmailDeliveryService, "load_global_smtp_settings", classmethod(_fake_load_global)
    )
    monkeypatch.setattr(EmailDeliveryService, "send_mail", classmethod(_boom))
    try:
        resp = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, "SMTP 异常不能变成 500"
        assert resp.json()["ok"] is False
    finally:
        await _drop_user(name)


async def test_self_test_uses_freshly_read_email(
    client, monkeypatch, clear_mail_test_cooldown
):
    """改了邮箱后必须发往新地址。

    require_api_key 注入的 user dict 缓存在 Redis（TTL 1 小时），改邮箱后它仍是旧值 ——
    实现若图省事读 user["email"]，测试信就会一直发往旧地址。
    """
    name = _name("zmt_fresh")
    old_email = f"{name}-old@corp.example.com"
    new_email = f"{name}-new@corp.example.com"
    key = await _seed_user_with_key(name, email=old_email)
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        # 先访问一次 /me，把带旧邮箱的 user dict 灌进 Redis 缓存
        assert (await client.get(ME_URL, headers={"X-API-Key": key})).status_code == 200
        async with AsyncSessionLocal() as s:
            await s.execute(
                update(User).where(User.user_name == name).values(email=new_email)
            )
            await s.commit()

        resp = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text
        assert calls[0]["recipients"] == [new_email], "必须发往实时查库的新邮箱"
    finally:
        await _drop_user(name)


async def test_rejected_precondition_does_not_consume_cooldown(
    client, monkeypatch, clear_mail_test_cooldown
):
    """前置检查被拒（未设邮箱）不得消耗冷却 —— 用户补好设置应能立刻重试。

    这钉住的是检查顺序：若把冷却检查提到前置检查之前，用户每失败一次就要干等 60 秒。
    """
    name = _name("zmt_noconsume")
    key = await _seed_user_with_key(name, email=None)
    calls = _stub_global_smtp(monkeypatch, available=True)
    try:
        rejected = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert rejected.status_code == 400, rejected.text
        assert calls == []

        async with AsyncSessionLocal() as s:
            await s.execute(
                update(User).where(User.user_name == name).values(
                    email=f"{name}@corp.example.com"
                )
            )
            await s.commit()

        resp = await client.post(TEST_MAIL_URL, headers={"X-API-Key": key})
        assert resp.status_code == 200, "被拒的前置检查不该消耗冷却"
        assert len(calls) == 1
    finally:
        await _drop_user(name)
