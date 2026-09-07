"""Tests for memory_search date parsing and daily summary integration."""
import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.core.context import AgentContext, set_agent_context
from app.services.ai.tools.memory_search_tool import (
    _is_recency_only_query,
    memory_search,
    parse_date_from_query,
)


def test_parse_date_from_query():
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    day_before_yesterday_str = (date.today() - timedelta(days=2)).isoformat()
    three_days_ago_str = (date.today() - timedelta(days=3)).isoformat()

    # 1. 相对时间词
    assert parse_date_from_query("今天我们聊了啥") == today_str
    assert parse_date_from_query("昨天聊了什么") == yesterday_str
    assert parse_date_from_query("前天的会议") == day_before_yesterday_str
    assert parse_date_from_query("3天前我们讨论的机房") == three_days_ago_str

    # 2. 绝对日期格式 (假设当前年为 2026 年)
    # 我们先临时 patch date.today 使得年份固定为 2026，方便验证绝对日期解析
    with patch("app.services.ai.tools.memory_search_tool.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 29)
        mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
        
        # 匹配 2026-05-28
        assert parse_date_from_query("2026-05-28 聊了啥") == "2026-05-28"
        # 匹配 5月20日
        assert parse_date_from_query("我们在5月20号说了什么") == "2026-05-20"
        # 匹配 5-18
        assert parse_date_from_query("回顾 5-18") == "2026-05-18"
        # 匹配 5.15
        assert parse_date_from_query("回顾 5.15 的内容") == "2026-05-15"

    # 3. 普通查询 (无时间词)
    assert parse_date_from_query("机房故障怎么处理") is None
    assert parse_date_from_query("") is None
    assert parse_date_from_query(None) is None


@pytest.mark.asyncio
async def test_memory_search_with_relative_date_integration():
    ctx = AgentContext(
        agent_id="a1",
        agent_name="test",
        user_id=100,
        conversation_id="conv-current",
    )
    set_agent_context(ctx)

    yesterday_str = (date.today() - timedelta(days=1)).isoformat()

    # Mock 每日摘要
    mock_daily = {
        "title": "昨日工作总结",
        "summary": "处理了机房断电故障，完成了数据平台的微服务部署。",
        "topics": json.dumps(["机房断电", "部署"]),
        "decisions": json.dumps(["迁移至2号机架"]),
        "open_items": json.dumps(["监控报警配置"]),
    }

    # Mock 当天会话摘要
    mock_sessions = [
        {
            "conversation_id": "conv-yesterday-1",
            "title": "机房故障排查会话",
            "summary": "用户反馈机房断电，确认原因为2号线故障，已临时切换。",
            "last_active": 1779976559,  # 某个时间戳
        }
    ]

    # Mock 全局召回（由于去重，排除掉已在当天列表展示的）
    mock_global_data = {
        "summaries": [
            {
                "conversation_id": "conv-yesterday-1",  # 应该被去重
                "title": "机房故障排查会话",
                "summary": "用户反馈机房断电，确认原因为2号线故障，已临时切换。",
                "score": 0.95,
                "last_active": 1779976559,
            },
            {
                "conversation_id": "conv-other-day",  # 保留
                "title": "数据库扩容讨论",
                "summary": "评估下周三对 MySQL 主库进行磁盘扩容的方案。",
                "score": 0.78,
                "last_active": 1779970000,
            }
        ],
        "history": []
    }

    with patch(
        "app.services.ai.tools.memory_search_tool.MemoryConfigService.get_bool",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.ai.tools.memory_search_tool.MemoryConfigService.get_int",
        new_callable=AsyncMock,
        return_value=5,
    ), patch(
        "app.services.ai.tools.memory_search_tool.DailySummaryService.get_daily_summary",
        new_callable=AsyncMock,
        return_value=mock_daily,
    ) as get_daily_mock, patch(
        "app.services.ai.tools.memory_search_tool.MemoryIndexService.list_session_summaries_for_day",
        new_callable=AsyncMock,
        return_value=mock_sessions,
    ) as list_day_mock, patch(
        "app.services.ai.tools.memory_search_tool.SessionSummaryService.search_for_user",
        new_callable=AsyncMock,
        return_value=mock_global_data,
    ) as search_user_mock:
        
        result = await memory_search.ainvoke({"scope": "summary", "query": "昨天聊了什么"})

        # 验证调用
        get_daily_mock.assert_awaited_once_with("100", yesterday_str)
        list_day_mock.assert_awaited_once_with("100", yesterday_str)
        search_user_mock.assert_awaited_once_with(
            user_id="100",
            query="昨天聊了什么",
            scope="summary",
            conversation_id=None,
            limit=5
        )

        # 验证返回内容
        assert f"## 目标日期 ({yesterday_str}) 的每日摘要" in result
        assert "昨日工作总结" in result
        assert "处理了机房断电故障，完成了数据平台的微服务部署。" in result
        assert "**讨论主题**: 机房断电, 部署" in result
        assert "**达成决策**: 迁移至2号机架" in result
        assert "**遗留待办**: 监控报警配置" in result

        assert f"## 目标日期 ({yesterday_str}) 的会话摘要列表" in result
        assert "机房故障排查会话" in result
        assert "conv-yesterday-1" in result

        assert "## 其他匹配的全局会话摘要" in result
        assert "数据库扩容讨论" in result
        assert "conv-other-day" in result
        # 验证去重：全局列表里不应包含 conv-yesterday-1 (因为它已被归在目标日期会话列表里)
        assert result.count("conv-yesterday-1") == 1


def test_is_recency_only_query():
    # 泛时间词应识别为 True
    assert _is_recency_only_query("最近") is True
    assert _is_recency_only_query("近期") is True
    assert _is_recency_only_query("上次") is True
    assert _is_recency_only_query("过往") is True
    assert _is_recency_only_query("历史对话") is True
    assert _is_recency_only_query("最近的对话") is True
    assert _is_recency_only_query("回顾对话") is True

    # 包含具体业务词的应为 False
    assert _is_recency_only_query("最近的机房故障") is False
    assert _is_recency_only_query("上次讨论的销售额") is False
    assert _is_recency_only_query("Docker 部署") is False
    assert _is_recency_only_query("") is False
    assert _is_recency_only_query(None) is False


@pytest.mark.asyncio
async def test_memory_search_recency_query_and_fallback():
    ctx = AgentContext(
        agent_id="a1",
        agent_name="test",
        user_id=100,
        conversation_id="conv-current",
    )
    set_agent_context(ctx)

    mock_recency_data = {
        "summaries": [
            {
                "conversation_id": "conv-recent-1",
                "title": "用户中心权限改造讨论",
                "summary": "梳理了 RBAC 权限与数据隔离方案",
                "last_active": 1772866800,
            }
        ],
        "history": [],
    }

    # 1. 验证传 query="最近" 时，自动转为 query=None 进行倒序检索
    with patch(
        "app.services.ai.tools.memory_search_tool.SessionSummaryService.search_for_user",
        new_callable=AsyncMock,
        return_value=mock_recency_data,
    ) as search_mock:
        result = await memory_search.ainvoke({"scope": "summary", "query": "最近"})
        search_mock.assert_awaited_once_with(
            user_id="100",
            query=None,
            scope="summary",
            conversation_id=None,
            limit=5,
        )
        assert "## 最近活跃的会话摘要" in result
        assert "用户中心权限改造讨论" in result

    # 2. 验证具体关键词未命中任何结果时，自动兜底拉取最近记录
    with patch(
        "app.services.ai.tools.memory_search_tool.SessionSummaryService.search_for_user",
        new_callable=AsyncMock,
        side_effect=[
            {"summaries": [], "history": []},  # 第一次根据 "kubernetes" 没搜到
            mock_recency_data,                   # 第二次兜底回退按时间拉最近
        ],
    ) as search_mock:
        result = await memory_search.ainvoke({"scope": "summary", "query": "kubernetes"})
        assert search_mock.await_count == 2
        assert "未找到与关键词「kubernetes」直接相关的历史会话，已为您呈现最近活跃的会话摘要" in result
        assert "用户中心权限改造讨论" in result
