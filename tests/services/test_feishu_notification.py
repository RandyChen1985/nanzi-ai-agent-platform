import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.notification_service import NotificationService
from app.services.task_notification_channels import (
    CHANNEL_SPECS,
    VALID_CHANNEL_IDS,
    normalize_notification_channels,
    build_notification_delivery_supplement,
)
from app.services.task_notification_delivery import _EXTERNAL_SENDERS
from app.services.ai.tools.notification_tools import send_feishu_message


def test_feishu_in_task_notification_channels():
    assert "feishu" in VALID_CHANNEL_IDS
    assert "feishu" in CHANNEL_SPECS
    tool_name, label, hint = CHANNEL_SPECS["feishu"]
    assert tool_name == "send_feishu_message"
    assert label == "飞书"

    normalized = normalize_notification_channels(["portal", "feishu", "unknown"])
    assert normalized == ["portal", "feishu"]

    supplement = build_notification_delivery_supplement(["feishu"])
    assert "飞书" in supplement
    assert "send_feishu_message" in supplement


def test_feishu_in_external_senders():
    assert "feishu" in _EXTERNAL_SENDERS
    assert _EXTERNAL_SENDERS["feishu"] == "send_feishu"


def test_feishu_default_config():
    assert "feishu" in NotificationService.DEFAULT_CONFIGS
    cfg = NotificationService.DEFAULT_CONFIGS["feishu"]
    assert cfg["is_enabled"] is False
    assert cfg["webhook_url"] == ""
    assert cfg["secret"] == ""


@pytest.mark.asyncio
async def test_feishu_test_connection_without_webhook():
    ok, err = await NotificationService.test_connection("feishu", {"webhook_url": ""})
    assert ok is False
    assert "不能为空" in err


@pytest.mark.asyncio
async def test_feishu_test_connection_success():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 0, "msg": "success"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        ok, err = await NotificationService.test_connection(
            "feishu",
            {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx", "secret": "my-secret"},
        )
        assert ok is True
        assert err == ""
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["msg_type"] == "interactive"
        assert "timestamp" in payload
        assert "sign" in payload
        assert payload["card"]["header"]["title"]["content"] == "消息通知连通性测试"
        assert payload["card"]["schema"] == "2.0"
        assert payload["card"]["body"]["elements"][0]["tag"] == "markdown"


@pytest.mark.asyncio
async def test_feishu_send_msg_real_failure():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 19001, "msg": "sign match fail"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        ok, err = await NotificationService._send_feishu_msg_real(
            {"webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"},
            "任务测试",
            "测试正文",
        )
        assert ok is False
        assert "sign match fail" in err


@pytest.mark.asyncio
async def test_feishu_tool_execution():
    tool = send_feishu_message()
    assert tool.name == "send_feishu_message"

    # When no agent context is present
    res = await tool._arun("标题", "正文")
    assert "无法确定当前用户" in res
