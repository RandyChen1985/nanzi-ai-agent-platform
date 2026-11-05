"""概览 / 运营分析页的区间参数契约。

## 背景

三个页面的时间筛选统一换成日历面板后，后端不能只改 Token 统计那一组接口：

- 概览：`/admin-stats`、`/user-stats`、`/agent-stats`
- 运营分析：`/ragflow/metrics/summary`（本就支持日期）、`/ragflow/retrieval-stats`（只吃 days）

`retrieval-stats` 用的是「最近 N 天」滚动窗口，如果不跟着区间走，选了 9 月 1~15 日
时「检索质量」卡还会显示最近 7 天的数据，与页面顶部写的区间直接打架。

## 断言方式

库里本来就有历史数据，所以不用「区间内必须恰好 N 条」来断言，而是**插一条再查增量**：
先取基线，插入后必须 +1；插入到区间外时，查询结果必须不变。
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.audit import AccessLog, AgentExecutionHistory, AgentExecutionTrace
from app.models.knowledge import KnowledgeRetrievalLog

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / "app/api/portal/endpoints/dashboard.py"
RAGFLOW = ROOT / "app/api/portal/endpoints/ragflow.py"
RETRIEVAL_SERVICE = ROOT / "app/services/knowledge_retrieval_log_service.py"

ENDPOINT_PREFIX = "/test-overview-range"
TRACE_PREFIX = "trace-test-overview-range-"
QUERY_PREFIX = "test-overview-range-"


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(AccessLog).where(AccessLog.endpoint.like(f"{ENDPOINT_PREFIX}%")))
        await session.execute(
            delete(AgentExecutionTrace).where(AgentExecutionTrace.trace_id.like(f"{TRACE_PREFIX}%"))
        )
        await session.execute(
            delete(AgentExecutionHistory).where(AgentExecutionHistory.trace_id.like(f"{TRACE_PREFIX}%"))
        )
        await session.execute(
            delete(KnowledgeRetrievalLog).where(KnowledgeRetrievalLog.query.like(f"{QUERY_PREFIX}%"))
        )
        await session.commit()


async def _add_access_log(user_name: str, when: datetime) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            AccessLog(
                trace_id=f"{TRACE_PREFIX}access",
                user_name=user_name,
                endpoint=f"{ENDPOINT_PREFIX}/ping",
                method="GET",
                status_code=200,
                process_time_ms=10.0,
                created_at=when,
            )
        )
        await session.commit()


async def _add_retrieval_log(when: datetime) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            KnowledgeRetrievalLog(
                query=f"{QUERY_PREFIX}{when.isoformat()}",
                hit_count=0,
                latency_ms=120,
                source="agent",
                created_at=when,
            )
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# 源码契约
# --------------------------------------------------------------------------- #


def test_overview_endpoints_share_the_window_resolver():
    """概览三个接口必须用同一个窗口解析，不然又是三套口径。"""
    source = DASHBOARD.read_text(encoding="utf-8")

    assert "def _resolve_stats_window(" in source
    for func in ("get_admin_stats", "get_user_stats", "get_agent_stats"):
        body = source[source.index(f"async def {func}(") :]
        body = body[: body.index("\n@router.get(")]
        assert "_resolve_stats_window(period, start_date, end_date)" in body, f"{func} 未走统一窗口"


def test_retrieval_stats_accepts_explicit_range():
    """「最近 N 天」的滚动窗口无法表达任意区间，必须支持显式日期。"""
    api_source = RAGFLOW.read_text(encoding="utf-8")
    service_source = RETRIEVAL_SERVICE.read_text(encoding="utf-8")

    assert "start_date: Optional[str] = None" in api_source
    assert "KnowledgeRetrievalLogService.stats(" in api_source
    assert "start_date: Optional[str] = None" in service_source
    assert "KnowledgeRetrievalLog.created_at <= until" in service_source


# --------------------------------------------------------------------------- #
# 概览接口
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_admin_stats_counts_only_the_selected_range(client: AsyncClient, admin_api_key: str):
    await _cleanup()
    headers = {"X-API-Key": admin_api_key}
    url = "/api/portal/dashboard/admin-stats"
    try:
        yesterday = datetime.now() - timedelta(days=1)
        base = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert base.status_code == 200, base.text
        baseline = base.json()["api_calls"]["total"]

        await _add_access_log("test_user", yesterday)
        await _add_access_log("test_user", datetime.now() - timedelta(days=40))

        after = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["api_calls"]["total"] == baseline + 1, "只能算进昨天那条"

        old = await client.get(url, params={"start_date": _day(-41), "end_date": _day(-39)}, headers=headers)
        assert old.status_code == 200, old.text
        assert old.json()["api_calls"]["total"] >= 1, "40 天前那条应落在旧区间"
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_user_stats_counts_only_the_selected_range(client: AsyncClient, valid_api_key: str):
    await _cleanup()
    headers = {"X-API-Key": valid_api_key}
    url = "/api/portal/dashboard/user-stats"
    try:
        base = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert base.status_code == 200, base.text
        baseline = base.json()["api_calls"]["total"]

        await _add_access_log("test_user", datetime.now() - timedelta(days=1))
        await _add_access_log("test_user", datetime.now() - timedelta(days=40))

        after = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["api_calls"]["total"] == baseline + 1
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_agent_stats_counts_only_the_selected_range(client: AsyncClient, admin_api_key: str):
    await _cleanup()
    headers = {"X-API-Key": admin_api_key}
    url = "/api/portal/dashboard/agent-stats"
    try:
        base = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert base.status_code == 200, base.text
        baseline = base.json()["health_stats"]["total_tool_calls"]

        async with AsyncSessionLocal() as session:
            for offset, tag in ((1, "in"), (40, "out")):
                trace_id = f"{TRACE_PREFIX}{tag}"
                when = datetime.now() - timedelta(days=offset)
                session.add_all(
                    [
                        AgentExecutionTrace(
                            trace_id=trace_id, step_number=1, event_type="tool_call",
                            tool_name="test_tool", status="success", execution_time_ms=100.0, created_at=when,
                        ),
                        AgentExecutionTrace(
                            trace_id=trace_id, step_number=2, event_type="tool_result",
                            tool_name="test_tool", status="success", execution_time_ms=100.0, created_at=when,
                        ),
                        AgentExecutionHistory(
                            agent_id="agent-overview-range", trace_id=trace_id, username="test_user",
                            prompt_tokens=1, completion_tokens=1, total_tokens=2,
                            status="success", created_at=when,
                        ),
                    ]
                )
            await session.commit()

        after = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["health_stats"]["total_tool_calls"] == baseline + 1, "区间外那组不应被计入"
    finally:
        await _cleanup()


# --------------------------------------------------------------------------- #
# 运营分析：检索质量
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_retrieval_stats_counts_only_the_selected_range(client: AsyncClient, admin_api_key: str):
    await _cleanup()
    headers = {"X-API-Key": admin_api_key}
    url = "/api/portal/ragflow/retrieval-stats"
    try:
        base = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        if base.status_code == 403:
            pytest.skip("当前测试账号不是知识库管理员")
        assert base.status_code == 200, base.text
        baseline = base.json()["data"]["total_queries"]

        await _add_retrieval_log(datetime.now() - timedelta(days=1))
        await _add_retrieval_log(datetime.now() - timedelta(days=40))

        after = await client.get(url, params={"start_date": _day(-1), "end_date": _day(-1)}, headers=headers)
        assert after.status_code == 200, after.text
        payload = after.json()["data"]
        assert payload["total_queries"] == baseline + 1
        assert payload["days"] == 1, "返回的天数要跟区间一致，页面标题直接用它"
    finally:
        await _cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"start_date": _day(-1)},
        {"end_date": _day(-1)},
        {"start_date": _day(-1), "end_date": _day(-3)},
        {"start_date": _day(-400), "end_date": _day(0)},
        {"start_date": "2026/11/01", "end_date": _day(0)},
    ],
)
async def test_invalid_range_is_rejected(client: AsyncClient, admin_api_key: str, params: dict):
    headers = {"X-API-Key": admin_api_key}
    for url in (
        "/api/portal/dashboard/admin-stats",
        "/api/portal/dashboard/user-stats",
        "/api/portal/dashboard/agent-stats",
    ):
        response = await client.get(url, params=params, headers=headers)
        assert response.status_code == 400, f"{url} 应拒绝非法区间：{params} -> {response.status_code}"
