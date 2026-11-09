"""已知凭据值的收集器。

落库前的指纹扫描需要知道「当前平台上有哪些值算凭据」。P0 阶段这些值分别躺在：

* ``system_configs.is_secret = true`` 的配置里（模型 API Key、请求签名密钥等）；
* ``sys_mcp_servers.auth_headers`` 里（静态认证头，库内加密、读出解密）。

两者都是「平台自己知道、但对落库内容而言是秘密」的值，正是工具入参/出参里最容易
被顺手带出来的东西——模型只要在工具结果里回显一次，就会明文进审计表。

收集结果带 60 秒进程内缓存，与 ``ConfigService`` 的配置缓存对齐：配置改了最多陈旧
一分钟，但不会让每一条 trace 都去查一次库。

**降级策略**：收集失败时沿用上一份快照（可能是空列表）并只缩短下次重试间隔，绝不
向上抛。宁可这一批少脱敏几条，也不能因为查配置失败就把整轮审计写入搞挂——审计是
排查问题的最后一道手段，它自己不能先宕。
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

logger = logging.getLogger(__name__)

#: 快照缓存时长（秒）。与 ConfigService 的 60s 配置缓存保持一致。
_SNAPSHOT_TTL_SECONDS = 60.0

#: 收集失败后的重试间隔（秒）。比正常 TTL 短得多，避免长时间停留在降级状态。
_RETRY_AFTER_FAILURE_SECONDS = 5.0

#: 这些是「尚未填写」的占位标记，不是真实凭据值，必须跳过。
_PLACEHOLDER_PREFIXES = ("${", "{{", "__")


def _looks_like_placeholder(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    return stripped.startswith(_PLACEHOLDER_PREFIXES)


class SecretRegistry:
    """把散落在各处的「平台已知秘密」收敛成 ``[(标签, 明文值)]`` 列表。"""

    def __init__(self) -> None:
        self._cache: list[tuple[str, str]] | None = None
        self._cached_at: float = 0.0

    def invalidate(self) -> None:
        """配置或 MCP 凭证变更后主动失效，避免测试与运维场景等到 TTL 才生效。"""
        self._cache = None
        self._cached_at = 0.0

    async def snapshot(self, *, force: bool = False) -> list[tuple[str, str]]:
        now = time.monotonic()
        if (
            not force
            and self._cache is not None
            and (now - self._cached_at) < _SNAPSHOT_TTL_SECONDS
        ):
            return self._cache

        try:
            collected = await self._collect()
        except Exception as exc:  # noqa: BLE001 - 收集失败不能影响调用方
            logger.warning(
                "[SecretRegistry] 收集已知凭据失败，本批沿用上一份快照（%d 条）: %s",
                len(self._cache or ()),
                exc,
            )
            self._cached_at = now - _SNAPSHOT_TTL_SECONDS + _RETRY_AFTER_FAILURE_SECONDS
            return self._cache or []

        self._cache = collected
        self._cached_at = now
        return collected

    async def _collect(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        pairs.extend(await self._from_system_configs())
        pairs.extend(await self._from_mcp_servers())
        return pairs

    async def _from_system_configs(self) -> Iterable[tuple[str, str]]:
        from app.services.config_service import ConfigService

        configs = await ConfigService.get_all_from_db()
        pairs: list[tuple[str, str]] = []
        for key, meta in (configs or {}).items():
            if not isinstance(meta, dict) or not meta.get("is_secret"):
                continue
            value = meta.get("value")
            if not isinstance(value, str) or _looks_like_placeholder(value):
                continue
            pairs.append((f"config.{key}", value))
        return pairs

    async def _from_mcp_servers(self) -> Iterable[tuple[str, str]]:
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.mcp import McpServer
        from app.services.mcp.mcp_auth_policy import resolve_mcp_auth_headers

        pairs: list[tuple[str, str]] = []
        async with AsyncSessionLocal() as session:
            servers = (await session.execute(select(McpServer))).scalars().all()

        for server in servers:
            try:
                headers = resolve_mcp_auth_headers(server) or {}
            except Exception as exc:  # noqa: BLE001 - 单条脏数据不影响其它服务
                logger.debug(
                    "[SecretRegistry] 跳过 MCP 服务 %s 的认证头: %s",
                    getattr(server, "id", "?"),
                    exc,
                )
                continue
            server_name = getattr(server, "server_name", None) or getattr(server, "id", "mcp")
            for header_name, header_value in headers.items():
                if not isinstance(header_value, str) or _looks_like_placeholder(header_value):
                    continue
                pairs.append((f"mcp.{server_name}.{header_name}", header_value))
        return pairs


secret_registry = SecretRegistry()
