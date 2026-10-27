"""SSO 管理接口必须把同步 requests 调用移出事件循环。

回归背景：``LaplacePortalApiClient.get_all_users()`` 内部是同步 ``requests.post``
（超时曾误写成 30000 秒），直接在 ``async def`` 里调用会让整个 uvicorn 事件循环
停摆——所有数据库查询一起无响应，看起来就像 MySQL 挂了。调用方必须用
``asyncio.to_thread`` 隔离。
"""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.api.portal.endpoints import management

pytestmark = pytest.mark.no_infrastructure


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


async def _sso_enabled(_key: str) -> str:
    return "true"


async def test_get_sso_users_does_not_block_event_loop(monkeypatch):
    def slow_get_all_users():
        time.sleep(0.4)
        return []

    monkeypatch.setattr(
        "app.services.config_service.ConfigService.get", _sso_enabled
    )
    monkeypatch.setattr(
        management.LaplacePortalApiClient,
        "get_all_users",
        staticmethod(slow_get_all_users),
    )

    db = AsyncMock()
    db.execute.return_value = _EmptyResult()

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        result = await management.get_sso_users(admin={}, db=db)
    finally:
        ticker_task.cancel()

    assert result == {"items": []}
    # 0.4s 的同步阻塞期间事件循环仍在调度；若没走 to_thread，ticks 会是 0。
    assert ticks >= 5
