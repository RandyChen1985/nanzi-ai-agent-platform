"""NanZi Platform MCP 专属服务配置。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm import AsyncSessionLocal
from app.models.platform_mcp import McpPlatformConfig


MCP_PLATFORM_CONFIG_ID = 1
MCP_PLATFORM_CONFIG_FIELDS = (
    "platform_enabled",
    "agent_enabled",
    "conversation_enabled",
    "knowledge_enabled",
    "metadata_enabled",
    "rate_limit_client_per_minute",
    "rate_limit_user_per_minute",
)


class PlatformMcpConfigService:
    """读写 Platform MCP 单例配置，不依赖通用配置表。"""

    @staticmethod
    async def get(db: AsyncSession) -> McpPlatformConfig | None:
        return (
            await db.execute(
                select(McpPlatformConfig).where(McpPlatformConfig.id == MCP_PLATFORM_CONFIG_ID)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_or_create(db: AsyncSession) -> McpPlatformConfig:
        config = await PlatformMcpConfigService.get(db)
        if config is None:
            config = McpPlatformConfig(id=MCP_PLATFORM_CONFIG_ID)
            db.add(config)
            await db.flush()
        return config

    @staticmethod
    async def get_flag(field_name: str) -> bool:
        if field_name not in MCP_PLATFORM_CONFIG_FIELDS:
            raise ValueError(f"Unsupported Platform MCP config field: {field_name}")
        async with AsyncSessionLocal() as db:
            config = await PlatformMcpConfigService.get(db)
            return bool(getattr(config, field_name, False)) if config else False

    @staticmethod
    def to_dict(config: McpPlatformConfig | None) -> dict[str, Any]:
        return {
            field_name: (
                int(getattr(config, field_name, 0) or 0)
                if field_name.startswith("rate_limit_")
                else bool(getattr(config, field_name, False))
            ) if config else (0 if field_name.startswith("rate_limit_") else False)
            for field_name in MCP_PLATFORM_CONFIG_FIELDS
        }


__all__ = [
    "MCP_PLATFORM_CONFIG_FIELDS",
    "MCP_PLATFORM_CONFIG_ID",
    "PlatformMcpConfigService",
]
