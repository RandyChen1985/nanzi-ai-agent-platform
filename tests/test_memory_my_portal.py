"""测试个人中心“我的记忆”隔离 API 端点"""
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException
from app.api.portal.endpoints.memory import (
    list_my_summaries,
    get_my_summary_detail,
    delete_my_summary,
    get_my_ltm,
    update_my_ltm,
    delete_my_ltm,
    LtmUpdateRequest
)

pytestmark = pytest.mark.no_infrastructure


@pytest.mark.asyncio
async def test_list_my_summaries():
    rows = [
        {
            "user_id": "7",
            "conversation_id": "conv-123",
            "summary_type": "session",
            "title": "测试会话",
            "summary": "这是当前用户的会话摘要",
            "last_active": 100,
        }
    ]
    with patch(
        "app.api.portal.endpoints.memory.MemoryIndexService.list_summaries",
        new_callable=AsyncMock,
        return_value=rows,
    ), patch(
        "app.api.portal.endpoints.memory._user_display_names",
        new_callable=AsyncMock,
        return_value={"7": {"user_name": "user_7", "display_name": "测试用户"}},
    ), patch(
        "app.api.portal.endpoints.memory.memory_service.history_exists",
        new_callable=AsyncMock,
        return_value=True,
    ):
        res = await list_my_summaries(
            keyword=None,
            limit=10,
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )
    
    assert res["status"] == "success"
    assert len(res["data"]) == 1
    assert res["data"][0]["user_id"] == "7"
    assert res["data"][0]["display_name"] == "测试用户"
    assert res["data"][0]["has_history"] is True


@pytest.mark.asyncio
async def test_get_my_summary_detail():
    summary_data = {
        "user_id": "7",
        "conversation_id": "conv-123",
        "title": "测试会话",
        "summary": "会话摘要"
    }
    history_data = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    with patch(
        "app.api.portal.endpoints.memory.MemoryIndexService.list_summaries",
        new_callable=AsyncMock,
        return_value=[summary_data],
    ), patch(
        "app.api.portal.endpoints.memory._user_display_names",
        new_callable=AsyncMock,
        return_value={"7": {"user_name": "user_7", "display_name": "测试用户"}},
    ), patch(
        "app.api.portal.endpoints.memory.memory_service.get_history",
        new_callable=AsyncMock,
        return_value=history_data,
    ):
        res = await get_my_summary_detail(
            conversation_id="conv-123",
            history_limit=30,
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )

    assert res["status"] == "success"
    assert res["data"]["summary"]["display_name"] == "测试用户"
    assert res["data"]["history"] == history_data
    assert res["data"]["has_history"] is True


@pytest.mark.asyncio
async def test_delete_my_summary():
    with patch(
        "app.api.portal.endpoints.memory.memory_service.delete_session_memory",
        new_callable=AsyncMock,
    ) as mock_delete:
        res = await delete_my_summary(
            conversation_id="conv-123",
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )

    assert res["status"] == "success"
    # 实现新增了 user_name 与 user_info 两个关键字参数（用于按用户维度清理）
    mock_delete.assert_awaited_once_with(
        "7",
        "conv-123",
        include_summary=True,
        user_name="user_7",
        user_info={"user_id": 7, "user_name": "user_7"},
    )


@pytest.mark.asyncio
async def test_get_my_ltm():
    ltm_data = {"theme": "dark", "language": "zh"}
    with patch(
        "app.api.portal.endpoints.memory.ltm_service.fetch_memory",
        new_callable=AsyncMock,
        return_value=ltm_data,
    ) as mock_fetch:
        res = await get_my_ltm(
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )

    assert res["status"] == "success"
    assert res["data"] == ltm_data
    mock_fetch.assert_awaited_once_with("7")


@pytest.mark.asyncio
async def test_update_my_ltm():
    with patch(
        "app.api.portal.endpoints.memory.ltm_service.update_preference",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_update:
        res = await update_my_ltm(
            body=LtmUpdateRequest(key="theme", value="light"),
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )

    assert res["status"] == "success"
    mock_update.assert_awaited_once_with("7", "theme", "light")

    # 测试空键名抛出 400
    with pytest.raises(HTTPException) as exc_info:
        await update_my_ltm(
            body=LtmUpdateRequest(key="  ", value="light"),
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_my_ltm():
    with patch(
        "app.api.portal.endpoints.memory.ltm_service.delete_preference",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_delete:
        res = await delete_my_ltm(
            key="theme",
            current_user={"user_id": 7, "user_name": "user_7"},
            _health={"ok": True},
        )

    assert res["status"] == "success"
    mock_delete.assert_awaited_once_with("7", "theme")
