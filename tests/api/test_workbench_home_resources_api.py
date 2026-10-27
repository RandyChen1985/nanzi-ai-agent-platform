"""工作台 /home：新增两个资源卡必须在真实数据下算出数字，而不是静默 error。

``_load_personal_resources`` 对单个 loader 的异常是 fail-soft 的（记日志 + status=error），
所以单看「字段存在」不够——这里断言状态不是 error。
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.auth_service import AuthService


@pytest.fixture
async def user_key(db_session):
    return await AuthService.generate_api_key(
        f"wb_{uuid.uuid4().hex[:8]}", role="user", db=db_session
    )


@pytest.mark.asyncio
async def test_home_returns_nine_cards_without_error(user_key):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            "/api/portal/workbench/home", headers={"X-API-Key": user_key}
        )
        assert resp.status_code == 200, resp.text
        cards = resp.json()["data"]["personal_resources"]
        by_key = {card["key"]: card for card in cards}

        assert len(cards) == 9
        assert cards[0]["key"] == "knowledge"
        assert cards[1]["key"] == "datasets"

        for key in ("knowledge", "datasets"):
            assert by_key[key]["status"] != "error", f"{key} 计数抛异常了"
            assert by_key[key]["tab"] == "square"
