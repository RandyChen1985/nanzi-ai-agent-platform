from typing import Any, Dict, Optional

from app.services.config_service import ConfigService

DEFAULT_PRODUCT_NAME = "NanZi·智能体平台"
DEFAULT_LOGIN_SUBTITLE = "NanZi Intelligent Agent Platform"
DEFAULT_ICON_URL = "/favicon.png"
DEFAULT_AGENT_NAME = "NanZi · AI"
# 历史默认名：读取时映射到新兜底，不写库
_LEGACY_DEFAULT_AGENT_NAMES = frozenset({"南孜智能助手", "南孜 · 智能助手"})


def _normalize_default_agent_name(value: Optional[str]) -> str:
    name = (value or "").strip()
    if not name or name in _LEGACY_DEFAULT_AGENT_NAMES:
        return DEFAULT_AGENT_NAME
    return name


class BrandingSettingsService:
    CONFIG_ENABLED = "branding.enabled"
    CONFIG_PRODUCT_NAME = "branding.product_name"
    CONFIG_LOGIN_SUBTITLE = "branding.login_subtitle"
    CONFIG_ICON_URL = "branding.icon_url"
    CONFIG_HIDE_LOGIN_SSO = "branding.hide_login_sso"
    CONFIG_HIDE_VERSION_LINK = "branding.hide_version_link"
    CONFIG_CONTACT_MARKDOWN = "branding.contact_markdown"
    CONFIG_COPYRIGHT_TEXT = "branding.copyright_text"
    CONFIG_DEFAULT_AGENT_NAME = "branding.default_agent_name"

    @staticmethod
    async def _get_bool(key: str, default: bool = False) -> bool:
        val = await ConfigService.get(key)
        if val is None:
            return default
        return str(val).strip().lower() == "true"

    @classmethod
    async def get_raw_settings(cls) -> Dict[str, Any]:
        return {
            "enabled": await cls._get_bool(cls.CONFIG_ENABLED, False),
            "product_name": (await ConfigService.get(cls.CONFIG_PRODUCT_NAME, DEFAULT_PRODUCT_NAME) or "").strip()
            or DEFAULT_PRODUCT_NAME,
            "login_subtitle": (await ConfigService.get(cls.CONFIG_LOGIN_SUBTITLE, DEFAULT_LOGIN_SUBTITLE) or "").strip()
            or DEFAULT_LOGIN_SUBTITLE,
            "icon_url": (await ConfigService.get(cls.CONFIG_ICON_URL, DEFAULT_ICON_URL) or "").strip()
            or DEFAULT_ICON_URL,
            "hide_login_sso": await cls._get_bool(cls.CONFIG_HIDE_LOGIN_SSO, False),
            "hide_version_link": await cls._get_bool(cls.CONFIG_HIDE_VERSION_LINK, False),
            "contact_markdown": (await ConfigService.get(cls.CONFIG_CONTACT_MARKDOWN, "")) or "",
            "copyright_text": (await ConfigService.get(cls.CONFIG_COPYRIGHT_TEXT, "")) or "",
            "default_agent_name": _normalize_default_agent_name(
                await ConfigService.get(cls.CONFIG_DEFAULT_AGENT_NAME, DEFAULT_AGENT_NAME)
            ),
        }

    @classmethod
    async def get_public_branding(cls) -> Dict[str, Any]:
        """前端展示用：未启用个性化时返回默认品牌，开关项均为默认展示行为。"""
        raw = await cls.get_raw_settings()
        if not raw["enabled"]:
            return {
                "enabled": False,
                "product_name": DEFAULT_PRODUCT_NAME,
                "login_subtitle": DEFAULT_LOGIN_SUBTITLE,
                "icon_url": DEFAULT_ICON_URL,
                "hide_login_sso": False,
                "hide_version_link": False,
                "contact_markdown": "",
                "copyright_text": "",
                "default_agent_name": DEFAULT_AGENT_NAME,
            }
        return {
            "enabled": True,
            "product_name": raw["product_name"],
            "login_subtitle": raw["login_subtitle"],
            "icon_url": raw["icon_url"],
            "hide_login_sso": raw["hide_login_sso"],
            "hide_version_link": raw["hide_version_link"],
            "contact_markdown": raw["contact_markdown"],
            "copyright_text": raw["copyright_text"],
            "default_agent_name": _normalize_default_agent_name(raw["default_agent_name"]),
        }

    @classmethod
    async def update_settings(
        cls,
        *,
        enabled: bool,
        product_name: str,
        login_subtitle: str,
        icon_url: str,
        hide_login_sso: bool,
        hide_version_link: bool,
        contact_markdown: str,
        copyright_text: str,
        default_agent_name: str,
        changed_by: str = "system",
    ) -> None:
        await ConfigService.set_config(
            cls.CONFIG_ENABLED,
            "true" if enabled else "false",
            description="是否启用品牌个性化",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_PRODUCT_NAME,
            (product_name or DEFAULT_PRODUCT_NAME).strip(),
            description="产品名称（浏览器标题、侧栏、登录页）",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_LOGIN_SUBTITLE,
            (login_subtitle or DEFAULT_LOGIN_SUBTITLE).strip(),
            description="登录页副标题",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_ICON_URL,
            (icon_url or DEFAULT_ICON_URL).strip(),
            description="Logo / Favicon 地址",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_HIDE_LOGIN_SSO,
            "true" if hide_login_sso else "false",
            description="登录页隐藏 SSO 登录",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_HIDE_VERSION_LINK,
            "true" if hide_version_link else "false",
            description="侧栏版本号取消 GitHub 外链",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_CONTACT_MARKDOWN,
            contact_markdown or "",
            description="联系信息 Markdown（个人中心 → 关于）",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_COPYRIGHT_TEXT,
            copyright_text or "",
            description="登录页底部版权文案",
            category="branding",
            changed_by=changed_by,
        )
        await ConfigService.set_config(
            cls.CONFIG_DEFAULT_AGENT_NAME,
            (default_agent_name or DEFAULT_AGENT_NAME).strip(),
            description="默认智能助手名称",
            category="branding",
            changed_by=changed_by,
        )
