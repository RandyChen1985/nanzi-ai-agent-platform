"""连通性测试文案：用户标识、时间戳与四渠道措辞一致性。"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.notification_service import (
    TEST_MESSAGE_TITLE,
    NotificationService,
    build_test_message,
    build_test_message_plain,
)

pytestmark = pytest.mark.no_infrastructure

_TIMESTAMP_RE = re.compile(r"触发时间\**：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
_ACTOR = {"user_name": "zhangsan", "real_name": "张三"}


def test_markdown_message_carries_actor_and_timestamp():
    body = build_test_message("dingtalk", _ACTOR)
    assert TEST_MESSAGE_TITLE in body
    assert "**触发用户**：张三（zhangsan）" in body
    assert _TIMESTAMP_RE.search(body)
    assert "**通知渠道**：钉钉" in body


def test_plain_message_avoids_markdown_markers():
    body = build_test_message_plain("email", _ACTOR)
    assert "#" not in body
    assert "**" not in body
    assert "触发用户：张三（zhangsan）" in body
    assert _TIMESTAMP_RE.search(body)
    assert "通知渠道：邮件" in body


def test_actor_display_deduplicates_identical_names():
    body = build_test_message("feishu", {"user_name": "zhangsan", "real_name": "zhangsan"})
    assert "**触发用户**：zhangsan" in body
    assert "zhangsan（zhangsan）" not in body


def test_actor_display_falls_back_to_user_name():
    body = build_test_message("feishu", {"user_name": "zhangsan"})
    assert "**触发用户**：zhangsan" in body


def test_message_without_actor_omits_actor_line():
    body = build_test_message("wechat_work")
    assert "触发用户" not in body
    # 时间与渠道仍需保留，保证消息可识别
    assert _TIMESTAMP_RE.search(body)
    assert "**通知渠道**：企业微信" in body


def test_all_channels_share_the_same_signature_copy():
    """四渠道共用模板，避免再次出现「您的AI」/「您的 AI」这类措辞漂移。"""
    for channel, label in (
        ("dingtalk", "钉钉"),
        ("wechat_work", "企业微信"),
        ("feishu", "飞书"),
        ("email", "邮件"),
    ):
        for body in (build_test_message(channel, _ACTOR), build_test_message_plain(channel, _ACTOR)):
            assert f"您的 AI 智能体平台个人中心{label}通知渠道已配置成功，测试消息发送正常。" in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel", "payload_path", "expected_label", "response"),
    (
        ("dingtalk", ("markdown", "text"), "钉钉", {"errcode": 0}),
        ("wechat_work", ("markdown", "content"), "企业微信", {"errcode": 0}),
        ("feishu", ("card", "body", "elements", 0, "content"), "飞书", {"code": 0}),
    ),
)
async def test_test_connection_embeds_actor_into_payload(channel, payload_path, expected_label, response):
    mock_resp = MagicMock()
    mock_resp.json.return_value = response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        ok, err = await NotificationService.test_connection(
            channel, {"webhook_url": "https://example.com/hook?token=x"}, _ACTOR
        )

    assert ok is True, err
    payload = mock_post.call_args[1]["json"]
    for key in payload_path:
        payload = payload[key]
    assert "张三（zhangsan）" in payload
    assert "通知渠道" in payload and expected_label in payload
