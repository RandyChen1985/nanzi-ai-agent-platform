import time
from typing import Any, Dict, Optional

from app.services.config_service import ConfigService

# 提示词中使用的平台品牌名：未启用品牌个性化时的兜底文案。
# 注意与前端展示用的 DEFAULT_PRODUCT_NAME 区分——后者用于侧栏/登录页 logo 文案。
DEFAULT_PLATFORM_BRAND_NAME = "NanZi智能体平台"

# 提示词品牌名缓存：提示词拼装链路是同步的，无法 await 配置读取，
# 因此这里维护进程内缓存，由异步侧刷新（启动预热 / 配置更新 / 前端拉取品牌时顺带刷新）。
_PROMPT_BRAND_NAME_TTL_SECONDS = 60.0

DEFAULT_PRODUCT_NAME = "NanZi AI Agent"
DEFAULT_LOGIN_SUBTITLE = "Your Intelligent Agent Platform"
DEFAULT_ICON_URL = "/favicon.svg"
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

    # 进程内提示词品牌名缓存（同步读取路径用）
    _prompt_brand_name: Optional[str] = None
    _prompt_brand_name_cached_at: float = 0.0
    # 单协程内正在刷新标志，避免缓存过期的瞬间被并发请求重复打穿配置读取
    _prompt_brand_name_refreshing: bool = False

    @classmethod
    def _store_prompt_brand_name(cls, raw: Dict[str, Any]) -> str:
        """把原始品牌设置折算成提示词品牌名并写入缓存。"""
        if raw.get("enabled"):
            name = (raw.get("product_name") or "").strip() or DEFAULT_PLATFORM_BRAND_NAME
        else:
            name = DEFAULT_PLATFORM_BRAND_NAME
        cls._prompt_brand_name = name
        cls._prompt_brand_name_cached_at = time.monotonic()
        return name

    @classmethod
    async def refresh_prompt_brand_name(cls) -> str:
        """异步刷新提示词品牌名缓存，返回最新品牌名。"""
        try:
            raw = await cls.get_raw_settings()
        except Exception:
            # 配置读取失败时保留旧值，仅重置时间戳避免高频重试
            cls._prompt_brand_name_cached_at = time.monotonic()
            return cls._prompt_brand_name or DEFAULT_PLATFORM_BRAND_NAME
        return cls._store_prompt_brand_name(raw)

    @classmethod
    async def refresh_prompt_brand_name_if_stale(cls) -> str:
        """缓存过期时刷新提示词品牌名，未过期直接返回缓存值。

        供异步请求入口调用（路由分诊），避免在同步提示词拼装路径里创建后台任务。
        """
        if (
            cls._prompt_brand_name is not None
            and (time.monotonic() - cls._prompt_brand_name_cached_at) < _PROMPT_BRAND_NAME_TTL_SECONDS
        ):
            return cls._prompt_brand_name
        if cls._prompt_brand_name_refreshing:
            # 已有协程在刷新：本次直接返回旧值，避免并发重复读取配置
            return cls._prompt_brand_name or DEFAULT_PLATFORM_BRAND_NAME
        cls._prompt_brand_name_refreshing = True
        try:
            return await cls.refresh_prompt_brand_name()
        finally:
            cls._prompt_brand_name_refreshing = False

    @classmethod
    def get_prompt_brand_name(cls) -> str:
        """同步读取提示词品牌名缓存（纯读、无副作用）。

        启用品牌个性化时返回 ``branding.product_name``，否则返回
        :data:`DEFAULT_PLATFORM_BRAND_NAME`。缓存由启动预热、保存品牌配置、
        前端拉取品牌信息以及 :meth:`refresh_prompt_brand_name_if_stale` 刷新。
        """
        return cls._prompt_brand_name or DEFAULT_PLATFORM_BRAND_NAME

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
        # 前端加载品牌信息时顺带刷新提示词品牌名缓存，减少一次配置读取。
        cls._store_prompt_brand_name(raw)
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
    async def reset_icon(cls, changed_by: str = "system") -> Dict[str, Any]:
        """把品牌图标还原为平台默认图标（``DEFAULT_ICON_URL``），返回最新原始配置。

        只重置 ``branding.icon_url`` 一个键，不影响其他品牌字段的编辑状态。
        """
        await ConfigService.set_config(
            cls.CONFIG_ICON_URL,
            DEFAULT_ICON_URL,
            description="Logo / Favicon 地址",
            category="branding",
            changed_by=changed_by,
        )
        return await cls.get_raw_settings()

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
        # 配置保存后立即刷新提示词品牌名，避免本进程内继续使用旧品牌名。
        await cls.refresh_prompt_brand_name()
