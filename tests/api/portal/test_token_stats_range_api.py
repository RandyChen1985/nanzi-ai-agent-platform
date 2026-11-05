"""Token 统计的区间参数契约（`/token-stats/agents`、`/token-stats/users`）。

## 背景

Token 统计页的时间选择从「今日 / 最近 7 天 / 最近 30 天」下拉改成了日历面板，
可以任选区间。但三个取数接口当初只对齐了两个：

- `/trends`、`/records` 早已支持 `start_date` / `end_date`；
- `/agents`、`/users` 只吃 `period=today|week|month`。

只改前端就会出现「趋势图是 9 月 1 日–10 月 15 日，指标卡和排行榜还是最近 7 天」
这种同屏两套口径，所以两个接口必须一起支持显式日期。

## 要求

1. 两个接口都接受 `start_date` / `end_date`，并且**真的按区间过滤**（上界也要有：
   原实现只有 `>= start_time`，显式区间时会把结束日之后的数据算进来）。
2. `period` 参数保留：总览页 `Overview.vue` 仍按 period 取智能体分布，不能改坏。
3. 只给一端日期 → 400，不能静默回落到 period（否则「选了半截区间」会变成
   「悄悄返回今日数据」）。
4. 非法日期格式 → 400 而不是 500。
"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory

ROOT = Path(__file__).resolve().parents[3]
DASHBOARD = ROOT / "app/api/portal/endpoints/dashboard.py"

TRACE_PREFIX = "trace-test-token-range-"
TEST_USERNAME = "token-range-test-user"
TEST_AGENT_ID = "agent-token-range-contract"


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AgentExecutionHistory).where(
                AgentExecutionHistory.trace_id.like(f"{TRACE_PREFIX}%")
            )
        )
        await session.commit()


async def _seed_history() -> None:
    """区间内一条（昨天）、区间外一条（40 天前）。"""
    now = datetime.now()
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AgentExecutionHistory(
                    agent_id=TEST_AGENT_ID,
                    trace_id=f"{TRACE_PREFIX}old",
                    username=TEST_USERNAME,
                    prompt_tokens=1000,
                    completion_tokens=100,
                    total_tokens=1100,
                    status="success",
                    created_at=now - timedelta(days=40),
                ),
                AgentExecutionHistory(
                    agent_id=TEST_AGENT_ID,
                    trace_id=f"{TRACE_PREFIX}recent",
                    username=TEST_USERNAME,
                    prompt_tokens=7,
                    completion_tokens=3,
                    total_tokens=10,
                    status="success",
                    created_at=now - timedelta(days=1),
                ),
            ]
        )
        await session.commit()


def test_all_stats_endpoints_share_one_window_resolver():
    """Token 统计、概览三个接口必须走同一个窗口解析函数，否则又会各写一套口径。"""
    source = DASHBOARD.read_text(encoding="utf-8")

    assert "def _resolve_stats_window(" in source
    # agents / users / admin-stats / user-stats / agent-stats 共 5 个入口
    assert source.count("_resolve_stats_window(period, start_date, end_date)") == 5, (
        "五个统计入口都应该调用 _resolve_stats_window"
    )


def test_window_resolver_rejects_half_open_range():
    """只给一端日期是调用错误：静默回落 period 会让用户以为区间生效了。"""
    source = DASHBOARD.read_text(encoding="utf-8")
    body = source[source.index("def _resolve_stats_window(") :]
    body = body[: body.index("@router.get(\"/admin-stats\")")]

    assert "if not (start_date and end_date):" in body
    assert "status_code=400" in body


@pytest.mark.asyncio
async def test_agents_endpoint_filters_by_explicit_range(client: AsyncClient, admin_api_key: str):
    """区间内的数据要算进来，区间外的不能混进来。"""
    await _cleanup()
    await _seed_history()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/agents",
            params={"start_date": _day(-3), "end_date": _day(0)},
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        rows = [row for row in response.json() if row["agent_id"] == TEST_AGENT_ID]

        assert rows, "区间内应包含刚插入的记录"
        assert rows[0]["calls"] == 1, "40 天前那条不应被计入"
        assert rows[0]["prompt_tokens"] == 7
        assert rows[0]["completion_tokens"] == 3
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_agents_endpoint_excludes_data_after_range_end(client: AsyncClient, admin_api_key: str):
    """上界必须生效：原实现只有下界，选「昨天」也会把今天的数据算进来。"""
    await _cleanup()
    await _seed_history()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/agents",
            params={"start_date": _day(-45), "end_date": _day(-30)},
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        rows = [row for row in response.json() if row["agent_id"] == TEST_AGENT_ID]

        assert rows, "40 天前那条应该被选中"
        assert rows[0]["calls"] == 1
        assert rows[0]["prompt_tokens"] == 1000, "昨天的记录不该越界出现"
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_users_endpoint_filters_by_explicit_range(client: AsyncClient, admin_api_key: str):
    await _cleanup()
    await _seed_history()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/users",
            params={"start_date": _day(-3), "end_date": _day(0)},
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        rows = [row for row in response.json() if row["username"] == TEST_USERNAME]

        assert rows, "区间内应包含刚插入的记录"
        assert rows[0]["calls"] == 1
        assert rows[0]["prompt_tokens"] == 7
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_period_parameter_still_works(client: AsyncClient, admin_api_key: str):
    """总览页仍按 period 取数，这条入口不能因为支持日期而失效。"""
    await _cleanup()
    await _seed_history()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/agents",
            params={"period": "today"},
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        rows = [row for row in response.json() if row["agent_id"] == TEST_AGENT_ID]
        assert rows == [], "period=today 不该包含昨天的记录"
    finally:
        await _cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"start_date": _day(-3)},  # 只给开始
        {"end_date": _day(0)},  # 只给结束
        {"start_date": _day(0), "end_date": _day(-3)},  # 结束早于开始
        {"start_date": _day(-120), "end_date": _day(0)},  # 超过 90 天
        {"start_date": "2026/11/01", "end_date": _day(0)},  # 格式不对
    ],
)
async def test_invalid_range_is_rejected_with_400(client: AsyncClient, admin_api_key: str, params: dict):
    for url in (
        "/api/portal/dashboard/token-stats/agents",
        "/api/portal/dashboard/token-stats/users",
    ):
        response = await client.get(url, params=params, headers={"X-API-Key": admin_api_key})
        assert response.status_code == 400, f"{url} 应拒绝非法区间：{params} -> {response.status_code}"
