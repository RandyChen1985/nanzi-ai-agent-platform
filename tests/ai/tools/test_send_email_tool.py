"""send_email 工具的收件人解析。

核心要求（用户明确强调）：**任何情况下都不许自己造一个收件人**。

- 显式给了收件人 → 就发给它，不加戏
- 没给 → 读用户在「个人中心 → 消息通知」里配置的收件人（默认含本人邮箱）
- 配置里也没有 → 报错提示去配置，**绝不**回退到 SMTP 发件账号之类的「看起来像个地址」
  的东西。悄悄发到别人/发件账号，比明确报错恶劣得多。
"""
import ast
import json

import pytest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai.tools.notification_tools import EmailInput, send_email
from app.services.email_delivery_service import EmailDeliveryService

pytestmark = pytest.mark.no_infrastructure

SELF = "me@corp.example.com"
SMTP_ACCOUNT = "sender@corp.example.com"
OTHER = "other@corp.example.com"


def _record(config: dict) -> MagicMock:
    rec = MagicMock()
    rec.config_json = json.dumps(config)
    return rec


@contextmanager
def _env(config: dict, *, self_email=SELF):
    """把工具的四个外部依赖都换掉，yield 出 send_mail 的 mock。"""
    ctx = MagicMock()
    ctx.user_id = 42
    db_cm = MagicMock()
    db_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    db_cm.__aexit__ = AsyncMock(return_value=None)
    # 发件账号故意「看起来可用」：如果实现拿它兜底，下面几条用例就能抓住
    settings = MagicMock()
    settings.effective_from = MagicMock(return_value=SMTP_ACCOUNT)
    send = MagicMock(return_value=(True, "ok"))
    with patch("app.core.context.get_current_agent_context", return_value=ctx), patch(
        "app.core.orm.AsyncSessionLocal", return_value=db_cm
    ), patch(
        "app.services.notification_service.NotificationService.get_config_by_type_raw",
        AsyncMock(return_value=_record(config)),
    ), patch(
        "app.services.email_delivery_service.EmailDeliveryService.resolve_smtp_settings",
        AsyncMock(return_value=settings),
    ), patch(
        "app.services.email_delivery_service.EmailDeliveryService.resolve_self_email",
        AsyncMock(return_value=self_email),
    ), patch(
        "app.services.email_delivery_service.EmailDeliveryService.send_mail", send
    ):
        yield send


def _recipients(send: MagicMock):
    return send.call_args.args[1]


def test_to_email_is_optional():
    """收件人必须是可选的：必填会逼模型追问，或更糟——凭空编一个地址。"""
    payload = EmailInput(subject="s", content="c")
    assert payload.to_email == ""


@pytest.mark.asyncio
async def test_explicit_recipient_is_used_exactly_as_given():
    """显式给了就照给，不擅自加上配置里的其他收件人或自己。"""
    tool = send_email()
    config = {"is_enabled": True, "smtp_source": "custom", "recipients": OTHER}
    with _env(config) as send:
        result = await tool._arun(
            to_email="explicit@corp.example.com", subject="s", content="c"
        )
    assert _recipients(send) == ["explicit@corp.example.com"]
    assert SMTP_ACCOUNT not in str(send.call_args)
    assert "Successfully sent" in result


@pytest.mark.asyncio
async def test_missing_recipient_reads_configured_recipients():
    """没给收件人时读用户配置：本人邮箱 + 其他收件人，与定时任务口径一致。"""
    tool = send_email()
    config = {"is_enabled": True, "smtp_source": "custom", "recipients": OTHER}
    with _env(config) as send:
        await tool._arun(to_email="", subject="s", content="c")
    assert _recipients(send) == [SELF, OTHER]


@pytest.mark.asyncio
async def test_missing_recipient_honours_include_self_false():
    tool = send_email()
    config = {
        "is_enabled": True,
        "smtp_source": "custom",
        "include_self": False,
        "recipients": OTHER,
    }
    with _env(config) as send:
        await tool._arun(subject="s", content="c")
    assert _recipients(send) == [OTHER]


@pytest.mark.asyncio
async def test_include_self_defaults_true_when_field_absent():
    """include_self 缺失按 True（新老用户一致），且不依赖 recipients 是否存在。"""
    tool = send_email()
    with _env({"is_enabled": True, "smtp_source": "custom"}) as send:
        await tool._arun(subject="s", content="c")
    assert _recipients(send) == [SELF]


@pytest.mark.asyncio
async def test_no_recipient_anywhere_errors_and_sends_nothing():
    """既没给、配置里也没有 → 报错要求配置，且**一个字节都不许发出去**。"""
    tool = send_email()
    config = {
        "is_enabled": True,
        "smtp_source": "custom",
        "include_self": False,
        "recipients": "",
    }
    with _env(config, self_email=None) as send:
        result = await tool._arun(subject="s", content="c")
    send.assert_not_called()
    assert result.startswith("Error:")
    low = result.lower()
    assert "收件人" in result or "recipient" in low, "必须明确提到收件人"
    assert "配置" in result or "configur" in low, "必须明确提示去配置"
    assert "@" not in result, "提示里不得出现任何像是收件人的地址"


@pytest.mark.asyncio
async def test_never_falls_back_to_smtp_sending_account():
    """自定义 SMTP 且收件人为空时，**不得**镜像定时任务那条「回退发给 smtp_user」。

    那条回退是给既有定时通知做兼容的；对 agent 主动发信，它等于「没说发给谁就发到
    发件账号」，属于凭空造收件人 —— 用户明确要求绝对不能发生。
    """
    tool = send_email()
    config = {
        "is_enabled": True,
        "smtp_source": "custom",
        "include_self": False,
        "recipients": "",
    }
    with _env(config, self_email=None) as send:
        result = await tool._arun(subject="s", content="c")
    send.assert_not_called()
    assert SMTP_ACCOUNT not in result
    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_invalid_explicit_recipient_is_rejected_before_sending():
    """显式传了但不含 @（模型写错/编错）→ 直接报错，不要拿到 SMTP 层去炸。"""
    tool = send_email()
    with _env({"is_enabled": True, "smtp_source": "custom"}) as send:
        result = await tool._arun(to_email="给张三发一封", subject="s", content="c")
    send.assert_not_called()
    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_explicit_recipient_accepts_multiple_separated_addresses():
    """写在一起的多个地址按配置同一套规则解析，不必分次调用。"""
    tool = send_email()
    with _env({"is_enabled": True, "smtp_source": "custom"}) as send:
        await tool._arun(
            to_email=f"a@corp.example.com, b@corp.example.com；{OTHER}",
            subject="s",
            content="c",
        )
    assert _recipients(send) == ["a@corp.example.com", "b@corp.example.com", OTHER]


def test_description_tells_model_to_leave_recipient_empty():
    """提示词必须告诉模型「留空 = 用户配置的收件人」，否则它仍会去猜地址。"""
    description = send_email().description
    assert "留空" in description
    assert "收件人" in description


def test_recipient_resolution_reuses_shared_helpers():
    """收件人解析只许走已存在的共享实现，不许在工具里另写一套。"""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[3]
        / "app/services/ai/tools/notification_tools.py"
    ).read_text(encoding="utf-8")
    idx = src.index("class send_email")
    body = src[idx : src.index("class WeChatWorkInput")]
    assert "resolve_self_email" in body, "必须复用 resolve_self_email（现查库，不读缓存）"
    assert "parse_recipients" in body or "compose_recipients" in body, (
        "必须复用共享的收件人解析/组合函数"
    )

    # 没有任何「兜底塞一个地址」的**代码**。用 AST 判定调用，而不是在源码文本里找名字：
    # 上面的 docstring 正解释着「不镜像回退发给 smtp_user」，文本匹配会把说明误判成实现。
    calls = {
        node.func.attr
        for node in ast.walk(ast.parse(body))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "effective_from" not in calls, (
        "不得回退到发件账号兜底（等于凭空造收件人）"
    )
