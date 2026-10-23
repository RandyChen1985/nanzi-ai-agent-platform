from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai.agent_prompts import AgentServicePrompts
from app.services.branding_settings_service import (
    BrandingSettingsService,
    DEFAULT_ICON_URL,
    DEFAULT_LOGIN_SUBTITLE,
    DEFAULT_PLATFORM_BRAND_NAME,
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
                "default_agent_name": "企业智能体",
            }
        ),
    ):
        result = await BrandingSettingsService.get_public_branding()

    assert result["enabled"] is True
    assert result["product_name"] == "企业智能体平台"
    assert result["hide_login_sso"] is True
    assert result["contact_markdown"] == "**技术支持**"
    assert result["copyright_text"] == "© 2026 Demo"
    assert result["default_agent_name"] == "企业智能体"


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
            default_agent_name="默认智能体",
            changed_by="admin",
        )

    assert mock_set.await_count == 9
    keys = [call.kwargs.get("key") or call.args[0] for call in mock_set.await_args_list]
    assert BrandingSettingsService.CONFIG_ENABLED in keys
    assert BrandingSettingsService.CONFIG_CONTACT_MARKDOWN in keys
    assert BrandingSettingsService.CONFIG_COPYRIGHT_TEXT in keys
    assert BrandingSettingsService.CONFIG_DEFAULT_AGENT_NAME in keys


@pytest.fixture(autouse=True)
def _isolate_prompt_brand_name_cache(monkeypatch):
    """隔离提示词品牌名进程内缓存，避免测试之间相互污染。"""
    monkeypatch.setattr(BrandingSettingsService, "_prompt_brand_name", None)
    monkeypatch.setattr(BrandingSettingsService, "_prompt_brand_name_cached_at", 0.0)
    monkeypatch.setattr(BrandingSettingsService, "_prompt_brand_name_refreshing", False)


def test_prompt_brand_name_falls_back_when_branding_disabled():
    BrandingSettingsService._store_prompt_brand_name(
        {"enabled": False, "product_name": "景普·智能体中心"}
    )

    assert BrandingSettingsService.get_prompt_brand_name() == DEFAULT_PLATFORM_BRAND_NAME


def test_prompt_brand_name_uses_product_name_when_enabled():
    BrandingSettingsService._store_prompt_brand_name(
        {"enabled": True, "product_name": "景普·智能体中心"}
    )

    assert BrandingSettingsService.get_prompt_brand_name() == "景普·智能体中心"


def test_prompt_brand_name_falls_back_when_enabled_but_blank():
    BrandingSettingsService._store_prompt_brand_name(
        {"enabled": True, "product_name": "   "}
    )

    assert BrandingSettingsService.get_prompt_brand_name() == DEFAULT_PLATFORM_BRAND_NAME


def test_platform_global_prompt_renders_default_brand():
    prompt = AgentServicePrompts.platform_global_system_prompt()

    assert prompt.startswith(f"[{DEFAULT_PLATFORM_BRAND_NAME} · 全局守则]")
    assert f"你是{DEFAULT_PLATFORM_BRAND_NAME}的对话助手" in prompt
    assert "{platform_brand_name}" not in prompt


def test_platform_global_prompt_renders_custom_brand():
    BrandingSettingsService._store_prompt_brand_name(
        {"enabled": True, "product_name": "景普·智能体中心"}
    )

    prompt = AgentServicePrompts.platform_global_system_prompt()

    assert prompt.startswith("[景普·智能体中心 · 全局守则]")
    assert "你是景普·智能体中心的对话助手" in prompt
    assert "{platform_brand_name}" not in prompt


def test_is_platform_global_prompt_ignores_brand_name():
    BrandingSettingsService._store_prompt_brand_name(
        {"enabled": True, "product_name": "景普·智能体中心"}
    )

    assert AgentServicePrompts.is_platform_global_prompt(
        AgentServicePrompts.platform_global_system_prompt()
    )
    assert not AgentServicePrompts.is_platform_global_prompt("# Active User Profile\n- name: x")
    assert not AgentServicePrompts.is_platform_global_prompt("")


def test_is_platform_global_prompt_handles_long_brand_and_mid_text():
    long_brand = "超长品牌" * 20  # 80 字
    long_prompt = AgentServicePrompts.platform_global_system_prompt(long_brand)
    assert AgentServicePrompts.is_platform_global_prompt(long_prompt)

    # 守则不在首行（多段落合并）时仍需识别
    merged = "# 其他段落\n\n" + AgentServicePrompts.platform_global_system_prompt()
    assert AgentServicePrompts.is_platform_global_prompt(merged)


def test_user_context_message_carries_brand_name():
    """回归：USER_PROFILE 前缀曾因批量改名出现 {platform_brand_name} 未定义变量（NameError）。"""
    BrandingSettingsService._store_prompt_brand_name(
        {"enabled": True, "product_name": "景普·智能体中心"}
    )

    message = AgentServicePrompts.user_context_message(user_id="u1", raw_name="tester")

    assert "由 景普·智能体中心 根据当前 API Key 会话身份注入" in message
