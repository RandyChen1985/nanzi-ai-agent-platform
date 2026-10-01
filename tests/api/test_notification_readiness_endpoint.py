"""接口契约：GET /api/portal/notifications/readiness。

前端两个任务编辑入口的勾选门禁都依赖这里的返回结构：每个渠道一个
{ready, reason}。结构一旦改动（比如漏掉某个渠道），前端会静默退化到本地兜底
判定，而那正是本次修复要消除的「界面自行推断」路径。
"""
import json
import uuid

import pytest
from sqlalchemy import delete, select

from app.core.orm import AsyncSessionLocal
from app.models.permission import UserRoleRelation
from app.models.user import User

READINESS_URL = "/api/portal/notifications/readiness"
CHANNELS = {"portal", "dingtalk", "wechat_work", "feishu", "email"}


async def _seed_email_user(user_name: str, config: dict) -> int:
    """建用户 + 写入其邮件通知配置，返回 user_id。"""
    from app.models.user_notification_config import UserNotificationConfig
    from app.services.auth_service import AuthService

    async with AsyncSessionLocal() as s:
        await AuthService.generate_api_key(
            user_name, real_name="渠道就绪测试", email=f"{user_name}@corp.example.com", db=s
        )
        uid = (await s.execute(select(User.id).where(User.user_name == user_name))).scalar_one()
        s.add(
            UserNotificationConfig(
                user_id=uid,
                channel_type="email",
                config_json=json.dumps(config, ensure_ascii=False),
            )
        )
        await s.commit()
        return uid


async def _drop_user(user_name: str) -> None:
    from app.models.user_notification_config import UserNotificationConfig

    async with AsyncSessionLocal() as s:
        uid = (await s.execute(select(User.id).where(User.user_name == user_name))).scalar()
        if uid is None:
            return
        await s.execute(
            delete(UserNotificationConfig).where(UserNotificationConfig.user_id == uid)
        )
        await s.execute(delete(UserRoleRelation).where(UserRoleRelation.user_id == uid))
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()


@pytest.mark.asyncio
async def test_readiness_returns_verdict_for_every_channel(client, valid_api_key):
    res = await client.get(READINESS_URL, headers={"X-API-Key": valid_api_key})
    assert res.status_code == 200, res.text
    channels = res.json()["channels"]
    assert set(channels) == CHANNELS, channels
    for name, verdict in channels.items():
        assert set(verdict) == {"ready", "reason"}, (name, verdict)
        assert isinstance(verdict["ready"], bool), (name, verdict)
        assert isinstance(verdict["reason"], str), (name, verdict)
        # 可用时不应带原因；不可用时必须有原因，否则前端只能显示一个哑掉的灰按钮
        assert bool(verdict["reason"]) is not verdict["ready"], (name, verdict)


@pytest.mark.asyncio
async def test_portal_is_always_ready(client, valid_api_key):
    res = await client.get(READINESS_URL, headers={"X-API-Key": valid_api_key})
    assert res.json()["channels"]["portal"] == {"ready": True, "reason": ""}


@pytest.mark.asyncio
async def test_readiness_requires_authentication(client):
    res = await client.get(READINESS_URL)
    assert res.status_code in (401, 403), res.status_code


@pytest.mark.asyncio
async def test_email_readiness_reads_real_config_and_follows_global_source(monkeypatch):
    """真实库 + 全局来源：个人没填 host/user 也算就绪，平台服务停用时才不可用。

    这是用户实际踩到的那条路径（界面说「未配置」，而个人配置本来就该是空的）。
    单测把配置读取 mock 掉了，这里补上真实 DB 读取这一段。
    """
    from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings
    from app.services.notification_service import NotificationService

    user_name = f"zreadi_{uuid.uuid4().hex[:8]}"
    try:
        uid = await _seed_email_user(user_name, {"is_enabled": True, "smtp_source": "global"})

        async def _global_ok():
            return SmtpSettings(
                host="smtp.corp.cn",
                port=465,
                user="bot@corp.cn",
                password="secret",
                security="ssl",
                from_address="bot@corp.cn",
                sender_name="AI Agent",
            )

        async def _global_disabled():
            return None

        monkeypatch.setattr(EmailDeliveryService, "load_global_smtp_settings", _global_ok)
        async with AsyncSessionLocal() as s:
            readiness = await NotificationService.get_channel_readiness(s, uid)
        assert readiness["email"]["ready"] is True, readiness["email"]

        monkeypatch.setattr(EmailDeliveryService, "load_global_smtp_settings", _global_disabled)
        async with AsyncSessionLocal() as s:
            readiness = await NotificationService.get_channel_readiness(s, uid)
        assert readiness["email"]["ready"] is False
        assert "平台尚未启用" in readiness["email"]["reason"], readiness["email"]["reason"]
    finally:
        await _drop_user(user_name)
