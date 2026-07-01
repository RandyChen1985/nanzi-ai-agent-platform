from unittest.mock import AsyncMock, patch

import pytest

from app.services.branding_settings_service import (
    BrandingSettingsService,
    DEFAULT_ICON_URL,
    DEFAULT_LOGIN_SUBTITLE,
    DEFAULT_PRODUCT_NAME,
)


@pytest.mark.asyncio
async def test_get_public_branding_disabled_uses_defaults():
    with patch.object(
        BrandingSettingsService,
        "get_raw_settings",
        new=AsyncMock(
            return_value={
                "enabled": False,
                "product_name": "自定义名称",
                "login_subtitle": "Custom",
                "icon_url": "/custom.png",
                "hide_login_sso": True,
                "hide_version_link": True,
                "contact_markdown": "联系管理员",
                "copyright_text": "© Test",
            }
        ),
    ):
        result = await BrandingSettingsService.get_public_branding()

    assert result["enabled"] is False
    assert result["product_name"] == DEFAULT_PRODUCT_NAME
    assert result["login_subtitle"] == DEFAULT_LOGIN_SUBTITLE
    assert result["icon_url"] == DEFAULT_ICON_URL
    assert result["hide_login_sso"] is False
    assert result["hide_version_link"] is False
    assert result["contact_markdown"] == ""
    assert result["copyright_text"] == ""


@pytest.mark.asyncio
async def test_get_public_branding_enabled_returns_custom():
    with patch.object(
        BrandingSettingsService,
        "get_raw_settings",
        new=AsyncMock(
            return_value={
                "enabled": True,
                "product_name": "企业智能体平台",
                "login_subtitle": "Enterprise Agent",
                "icon_url": "/branding/icon.png",
                "hide_login_sso": True,
                "hide_version_link": True,
                "contact_markdown": "**技术支持**",
                "copyright_text": "© 2026 Demo",
            }
        ),
    ):
        result = await BrandingSettingsService.get_public_branding()

    assert result["enabled"] is True
    assert result["product_name"] == "企业智能体平台"
    assert result["hide_login_sso"] is True
    assert result["contact_markdown"] == "**技术支持**"
    assert result["copyright_text"] == "© 2026 Demo"


@pytest.mark.asyncio
async def test_update_settings_persists_all_keys():
    with patch("app.services.branding_settings_service.ConfigService.set_config", new=AsyncMock()) as mock_set:
        await BrandingSettingsService.update_settings(
            enabled=True,
            product_name="A",
            login_subtitle="B",
            icon_url="/icon.png",
            hide_login_sso=True,
            hide_version_link=True,
            contact_markdown="hello",
            copyright_text="© Co",
            changed_by="admin",
        )

    assert mock_set.await_count == 8
    keys = [call.kwargs.get("key") or call.args[0] for call in mock_set.await_args_list]
    assert BrandingSettingsService.CONFIG_ENABLED in keys
    assert BrandingSettingsService.CONFIG_CONTACT_MARKDOWN in keys
    assert BrandingSettingsService.CONFIG_COPYRIGHT_TEXT in keys
