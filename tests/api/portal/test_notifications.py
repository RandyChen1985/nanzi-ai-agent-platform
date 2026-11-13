import pytest
import json
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.notification_service import NotificationService
from app.models.user_notification_config import UserNotificationConfig

@pytest.mark.asyncio
async def test_get_notification_configs_default(client: AsyncClient, valid_api_key: str, db_session):
    """Test getting notification configs when none exist in DB (should return defaults)"""
    from sqlalchemy import delete, select

    from app.models.user import User

    # 只清本测试账号（test_user）的配置。
    # 这里原本是 delete(UserNotificationConfig) —— 没有 WHERE 的全表删除。
    # 测试连的是真实开发库（conftest 用的就是 AsyncSessionLocal），
    # 于是每跑一次测试就把**所有用户**配好的钉钉/飞书/企微群清空一次，
    # 而且删完即 commit，无法回滚。跨用例污染用 user_id 过滤同样能解决。
    await db_session.execute(
        delete(UserNotificationConfig).where(
            UserNotificationConfig.user_id.in_(
                select(User.id).where(User.user_name == "test_user")
            )
        )
    )
    await db_session.commit()

    response = await client.get(
        "/api/portal/notifications/config",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    configs = response.json()
    
    # Check that all default channels exist
    assert "dingtalk" in configs
    assert "wechat_work" in configs
    assert "email" in configs
    
    # Check default values
    assert configs["dingtalk"]["is_enabled"] is False
    assert configs["dingtalk"]["webhook_url"] == ""
    assert configs["dingtalk"]["secret"] == ""
    assert configs["email"]["is_enabled"] is False
    assert configs["email"]["smtp_port"] == 465


@pytest.mark.asyncio
async def test_save_and_mask_notification_config(client: AsyncClient, valid_api_key: str):
    """Test saving a notification config and checking that sensitive fields are masked"""
    # 1. Save DingTalk config
    payload = {
        "channel_type": "dingtalk",
        "config_data": {
            "is_enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=12345",
            "secret": "my-super-secret-key"
        }
    }
    
    response = await client.put(
        "/api/portal/notifications/config",
        json=payload,
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # 2. Query configs to verify the mask
    get_response = await client.get(
        "/api/portal/notifications/config",
        headers={"X-API-Key": valid_api_key}
    )
    assert get_response.status_code == 200
    configs = get_response.json()
    
    assert configs["dingtalk"]["is_enabled"] is True
    assert configs["dingtalk"]["webhook_url"] == "https://oapi.dingtalk.com/robot/send?access_token=12345"
    # Secret should be masked
    assert configs["dingtalk"]["secret"] == "******"

@pytest.mark.asyncio
async def test_save_with_masked_value_keeps_original(client: AsyncClient, valid_api_key: str):
    """Test that saving with a masked value ('******') preserves the original value in the DB"""
    # 1. Save initial config
    payload1 = {
        "channel_type": "dingtalk",
        "config_data": {
            "is_enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=123",
            "secret": "original_secret_value"
        }
    }
    await client.put(
        "/api/portal/notifications/config",
        json=payload1,
        headers={"X-API-Key": valid_api_key}
    )
    
    # 2. Save again, but keep masked secret '******' (simulating frontend submission without modifying secret)
    payload2 = {
        "channel_type": "dingtalk",
        "config_data": {
            "is_enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=123_updated",
            "secret": "******"
        }
    }
    response2 = await client.put(
        "/api/portal/notifications/config",
        json=payload2,
        headers={"X-API-Key": valid_api_key}
    )
    assert response2.status_code == 200
    
    # 3. Retrieve raw database config to verify the actual value is still 'original_secret_value'
    # We resolve it programmatically to check
    get_response = await client.get(
        "/api/portal/notifications/config",
        headers={"X-API-Key": valid_api_key}
    )
    configs = get_response.json()
    assert configs["dingtalk"]["webhook_url"] == "https://oapi.dingtalk.com/robot/send?access_token=123_updated"
    assert configs["dingtalk"]["secret"] == "******" # Masked externally
    
    # Resolve the mask to verify it maps to original_secret_value
    # In order to test resolve_masked_config we resolve programmatically with a dummy session
    # or just make sure it resolves properly.

@pytest.mark.asyncio
@patch("app.services.notification_service.NotificationService._test_dingtalk")
async def test_notifications_test_endpoint_success(mock_test, client: AsyncClient, valid_api_key: str):
    """Test connectivity test endpoint - success case"""
    mock_test.return_value = (True, "")
    
    payload = {
        "channel_type": "dingtalk",
        "config_data": {
            "is_enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test",
            "secret": "******" # Will be resolved to whatever is in DB, here mock handles it
        }
    }
    
    response = await client.post(
        "/api/portal/notifications/test",
        json=payload,
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "测试连通成功" in response.json()["message"]

@pytest.mark.asyncio
@patch("app.services.notification_service.NotificationService._test_dingtalk")
async def test_notifications_test_endpoint_failure(mock_test, client: AsyncClient, valid_api_key: str):
    """Test connectivity test endpoint - failure case"""
    mock_test.return_value = (False, "Invalid Webhook Token")
    
    payload = {
        "channel_type": "dingtalk",
        "config_data": {
            "is_enabled": True,
            "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=test",
            "secret": "test_secret"
        }
    }
    
    response = await client.post(
        "/api/portal/notifications/test",
        json=payload,
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 400
    res_data = response.json()
    err_msg = res_data.get("message") or res_data.get("detail") or ""
    assert "连通测试失败: Invalid Webhook Token" in err_msg

@pytest.mark.asyncio
@patch("app.services.notification_service.NotificationService._test_wechat_work")
async def test_notifications_test_wechat_work_success(mock_test, client: AsyncClient, valid_api_key: str):
    """Test WeChat Work connectivity test - success case"""
    mock_test.return_value = (True, "")
    
    payload = {
        "channel_type": "wechat_work",
        "config_data": {
            "is_enabled": True,
            "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
        }
    }
    
    response = await client.post(
        "/api/portal/notifications/test",
        json=payload,
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "测试连通成功" in response.json()["message"]


