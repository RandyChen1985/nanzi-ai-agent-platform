"""平台对外访问地址的统一解析。

口径（全平台唯一来源）：

    系统配置 ``app_public_url``  →  环境变量 ``APP_PUBLIC_URL``  →  空串（相对地址）

历史上这个配置项叫 ``download_url_prefix``，只服务下载链接与密码重置；MCP、报表
通知、网页预览各自直接读 ``settings.APP_PUBLIC_URL``，导致「系统配置里填的公网
地址」在这些地方不生效。改名后统一走本模块，调用方不要再直接读环境变量。
"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

#: 系统配置表中的键名（与历史 download_url_prefix 是同一行数据，靠迁移脚本改名）
APP_PUBLIC_URL_CONFIG_KEY = "app_public_url"


def normalize_public_url(value: str | None) -> str:
    """去掉首尾空白与结尾斜杠，避免拼出 ``//api/...``。"""
    return str(value or "").strip().rstrip("/")


async def get_app_public_url() -> str:
    """按「系统配置 → 环境变量 → 空」解析对外访问地址。"""
    try:
        configured = await ConfigService.get(APP_PUBLIC_URL_CONFIG_KEY)
    except Exception as exc:  # noqa: BLE001
        # 配置表不可用（迁移未跑、DB 抖动）时降级到环境变量，不能让下载链接
        # 和密码重置链接跟着一起失败。
        logger.warning("Failed to read app_public_url from system config: %s", exc)
        configured = None

    return normalize_public_url(configured) or normalize_public_url(settings.APP_PUBLIC_URL)
