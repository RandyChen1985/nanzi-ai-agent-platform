"""通知渠道就绪判定（`/api/portal/notifications/readiness` 背后的唯一判据）。

守的是一类**界面说假话**的 bug：任务编辑弹窗原先只认 `smtp_host + smtp_user`
非空，而个人中心的邮件配置早已有「来源分层」——选「使用全局邮件服务」时个人配置
里本就没有 host/user，于是功能完全可用的渠道被锁死在灰复选框上，用户被引导去
反复重填一份本来就不需要填的表单。

这里的每个用例都锚定「readiness 的结论 == 发信路径的结论」，因为一旦两者分叉，
受伤的总是用户：要么勾了发不出去，要么能发却不让勾。
"""
import pytest

from app.services.email_delivery_service import EmailDeliveryService, SmtpSettings
from app.services.notification_service import NotificationService

pytestmark = pytest.mark.no_infrastructure

USER_ID = 4242


def _complete_settings() -> SmtpSettings:
    return SmtpSettings(
        host="smtp.corp.cn",
        port=465,
        user="bot@corp.cn",
        password="secret",
        security="ssl",
        from_address="bot@corp.cn",
        sender_name="AI Agent",
    )


def _patch_channels(monkeypatch, *, configs, smtp, self_email="me@corp.cn"):
    """把三个外部依赖（渠道原始配置 / SMTP 解析 / 本人邮箱）收敛成可控输入。"""

    async def _load_raw(db, user_id, channel_type):
        return dict(configs.get(channel_type) or {})

    async def _resolve_smtp(user_id=None, scope="user", db=None):
        return smtp

    async def _resolve_self_email(user_id, db):
        return self_email

    monkeypatch.setattr(NotificationService, "load_raw_channel_config", _load_raw)
    monkeypatch.setattr(EmailDeliveryService, "resolve_smtp_settings", _resolve_smtp)
    monkeypatch.setattr(EmailDeliveryService, "resolve_self_email", _resolve_self_email)


# --------------------------------------------------------------------------- #
# 邮件：全局来源（本 bug 的主角）
# --------------------------------------------------------------------------- #


async def test_email_is_ready_with_global_source_even_without_personal_smtp(monkeypatch):
    """选「全局邮件服务」时个人配置里没有 host/user，渠道仍必须判定为可投递。"""
    _patch_channels(
        monkeypatch,
        configs={"email": {"is_enabled": True, "smtp_source": "global"}},
        smtp=_complete_settings(),
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["email"]["ready"] is True, readiness["email"]
    assert readiness["email"]["reason"] == ""


async def test_email_global_source_reports_platform_service_disabled(monkeypatch):
    """全局服务没启用时，原因必须指向平台侧，而不是含糊的「未配置」。"""
    _patch_channels(
        monkeypatch,
        configs={"email": {"is_enabled": True, "smtp_source": "global"}},
        smtp=None,
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["email"]["ready"] is False
    assert "平台尚未启用" in readiness["email"]["reason"], readiness["email"]["reason"]


async def test_email_custom_source_without_host_is_not_ready(monkeypatch):
    """自定义来源才是「必须填服务器地址」的场景，且原因不能与全局混淆。"""
    _patch_channels(
        monkeypatch,
        configs={"email": {"is_enabled": True, "smtp_source": "custom", "smtp_host": ""}},
        smtp=None,
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["email"]["ready"] is False
    assert "自定义 SMTP" in readiness["email"]["reason"], readiness["email"]["reason"]


async def test_email_legacy_record_without_smtp_source_falls_back_by_host(monkeypatch):
    """存量记录没有 smtp_source：按 resolve_smtp_source 的自适应规则判定。

    老记录填过 host 就当自定义、没填就当全局——与发信路径完全一致，因此
    「只有 is_enabled、没有 host」的存量用户不该再被锁死。
    """
    _patch_channels(
        monkeypatch,
        configs={"email": {"is_enabled": True}},
        smtp=_complete_settings(),
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["email"]["ready"] is True, readiness["email"]


async def test_email_blocks_when_no_recipient_can_be_resolved(monkeypatch):
    """能连上 SMTP 也不等于发得出去：没有收件人时同样要挡住。"""
    _patch_channels(
        monkeypatch,
        configs={
            "email": {
                "is_enabled": True,
                "smtp_source": "global",
                "include_self": False,
                "recipients": "",
            }
        },
        smtp=_complete_settings(),
        self_email=None,
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["email"]["ready"] is False
    assert "收件人" in readiness["email"]["reason"], readiness["email"]["reason"]


async def test_send_email_and_readiness_share_one_verdict(monkeypatch):
    """**同源守卫**：readiness 的原因必须就是 send_email 实际返回的失败原因。

    两边各写一份判定正是本 bug 的成因；这条用例让任何一次单边修改立刻失败。
    """
    _patch_channels(
        monkeypatch,
        configs={"email": {"is_enabled": True, "smtp_source": "global"}},
        smtp=None,
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    ok, err = await NotificationService.send_email(None, USER_ID, "标题", "正文")
    assert ok is False
    assert err == readiness["email"]["reason"], (err, readiness["email"]["reason"])


# --------------------------------------------------------------------------- #
# Webhook 渠道与站内消息
# --------------------------------------------------------------------------- #


async def test_webhook_channels_explain_their_own_reason(monkeypatch):
    """三个 webhook 渠道各自给出可操作的原因，不是一句笼统的「未配置」。"""
    _patch_channels(
        monkeypatch,
        configs={
            "dingtalk": {"is_enabled": False, "webhook_url": "https://oapi.dingtalk.com/x"},
            "wechat_work": {"is_enabled": True, "webhook_url": "   "},
            "feishu": {"is_enabled": True, "webhook_url": "https://open.feishu.cn/x"},
        },
        smtp=None,
    )
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["dingtalk"] == {"ready": False, "reason": "未启用"}
    assert readiness["wechat_work"] == {"ready": False, "reason": "未填写 Webhook 地址"}
    assert readiness["feishu"] == {"ready": True, "reason": ""}


async def test_portal_is_always_ready(monkeypatch):
    """站内消息是平台默认通道，不该因为任何外部通道状态被牵连。"""
    _patch_channels(monkeypatch, configs={}, smtp=None)
    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["portal"] == {"ready": True, "reason": ""}


async def test_single_channel_probe_failure_does_not_lock_everything(monkeypatch):
    """单个渠道探测异常只降级它自己：读配置失败不等于其它渠道也不可用。"""

    async def _load_raw(db, user_id, channel_type):
        if channel_type == "dingtalk":
            raise RuntimeError("db blip")
        if channel_type == "email":
            return {"is_enabled": True, "smtp_source": "global"}
        return {"is_enabled": True, "webhook_url": "https://open.feishu.cn/x"}

    async def _resolve_smtp(user_id=None, scope="user", db=None):
        return _complete_settings()

    async def _resolve_self_email(user_id, db):
        return "me@corp.cn"

    monkeypatch.setattr(NotificationService, "load_raw_channel_config", _load_raw)
    monkeypatch.setattr(EmailDeliveryService, "resolve_smtp_settings", _resolve_smtp)
    monkeypatch.setattr(EmailDeliveryService, "resolve_self_email", _resolve_self_email)

    readiness = await NotificationService.get_channel_readiness(None, USER_ID)
    assert readiness["dingtalk"]["ready"] is False
    assert "读取失败" in readiness["dingtalk"]["reason"]
    assert readiness["feishu"]["ready"] is True
    assert readiness["email"]["ready"] is True
    assert readiness["portal"]["ready"] is True
