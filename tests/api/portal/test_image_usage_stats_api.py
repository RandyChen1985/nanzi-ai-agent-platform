"""图片用量统计接口（`/dashboard/token-stats/image-usage`）的契约。

## 背景

生图/改图的用量早已留痕在 `ai_agent_execution_traces.meta_info.image_usage`（张数、
尺寸、模型、上游折算 token、耗时），但它**刻意不进 `total_tokens`** —— 那个数驱动
月度配额，混进去会让「消息显示的用量」与「配额实际消耗」变成两套算法。

代价是现场只统计 Token 的页面里**看不到图片**，只能一轮一轮点进轨迹里查。本接口
就是补这个口子：从轨迹表直接聚合，**零 DDL、零新采集**。

## 三条刻意的口径，改之前先确认需求

1. **上游没返回 token 时不能记 0**：多数图像端点（OpenAI images）根本不返回
   `usage`，记 0 会被读成「这次没花钱」。所以单独给 `tokens_missing_calls` 计数，
   汇总里的 `upstream_tokens` 只累加真实返回过的那部分。
2. **失败张数 = `requested - succeeded`**：一次调用要 2 张只出 1 张，那一张也是
   真扣了钱，不能被「出图 1 张」盖掉。
3. **非管理员只能看自己**：轨迹表本身没有 username，靠 trace_id 关联历史表取；
   关联不上的（如定时任务）对普通用户一律不返回——无法证明归属就不能展示。

## 测试隔离

本仓库测试连的是真实开发库，所以种子数据放在**未来日期**（今天 +3 天）并配一个
刚好包住它的区间：真实数据不可能落在未来，这样汇总数字可以精确断言，也不会被
线上历史数据污染。
"""
import json
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.audit import AgentExecutionHistory, AgentExecutionTrace

TRACE_PREFIX = "trace-test-img-usage-"
OWNER = "test_user"
OTHER = "test_admin"
AGENT_OWNER = "agent-img-usage-contract-a"
AGENT_OTHER = "agent-img-usage-contract-b"


def _day(offset: int) -> str:
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _range() -> dict:
    """刚好包住未来种子的区间；真实库不可能有这个区间的数据。"""
    return {"start_date": _day(2), "end_date": _day(4)}


def _usage(
    *,
    tool: str = "generate_image",
    requested: int = 1,
    succeeded: int = 1,
    size: str = "1024x1024",
    total_tokens: int | None = 100,
    elapsed_ms: int = 1000,
) -> dict:
    return {
        "tool": tool,
        "requested_count": requested,
        "succeeded_count": succeeded,
        "size": size,
        "model": "gpt-image-1",
        "upstream_tokens": (
            None
            if total_tokens is None
            else {
                "prompt_tokens": 0,
                "completion_tokens": total_tokens,
                "total_tokens": total_tokens,
            }
        ),
        "elapsed_ms": elapsed_ms,
    }


async def _cleanup() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(AgentExecutionTrace).where(
                AgentExecutionTrace.trace_id.like(f"{TRACE_PREFIX}%")
            )
        )
        await session.execute(
            delete(AgentExecutionHistory).where(
                AgentExecutionHistory.trace_id.like(f"{TRACE_PREFIX}%")
            )
        )
        await session.commit()


async def _seed() -> None:
    """三条带用量（其中一条上游未返回 token）+ 一条不带用量的普通工具调用。"""
    stamp = datetime.now() + timedelta(days=3)
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AgentExecutionHistory(
                    agent_id=AGENT_OWNER,
                    trace_id=f"{TRACE_PREFIX}own-1",
                    username=OWNER,
                    status="success",
                    created_at=stamp,
                ),
                AgentExecutionHistory(
                    agent_id=AGENT_OWNER,
                    trace_id=f"{TRACE_PREFIX}own-2",
                    username=OWNER,
                    status="success",
                    created_at=stamp,
                ),
                AgentExecutionHistory(
                    agent_id=AGENT_OTHER,
                    trace_id=f"{TRACE_PREFIX}other-1",
                    username=OTHER,
                    status="success",
                    created_at=stamp,
                ),
                AgentExecutionHistory(
                    agent_id=AGENT_OWNER,
                    trace_id=f"{TRACE_PREFIX}own-3",
                    username=OWNER,
                    status="success",
                    created_at=stamp,
                ),
                # 一次要 2 张、只出 1 张，且上游没返回 usage
                AgentExecutionTrace(
                    trace_id=f"{TRACE_PREFIX}own-1",
                    step_number=7,
                    event_type="tool_call",
                    tool_name="generate_image",
                    execution_time_ms=1000,
                    status="success",
                    meta_info={"image_usage": _usage(requested=2, succeeded=2, total_tokens=100)},
                    created_at=stamp,
                ),
                AgentExecutionTrace(
                    trace_id=f"{TRACE_PREFIX}own-2",
                    step_number=7,
                    event_type="tool_call",
                    tool_name="generate_image",
                    execution_time_ms=3000,
                    status="error",
                    meta_info={"image_usage": _usage(requested=2, succeeded=1, total_tokens=None, elapsed_ms=3000)},
                    created_at=stamp,
                ),
                AgentExecutionTrace(
                    trace_id=f"{TRACE_PREFIX}other-1",
                    step_number=7,
                    event_type="tool_call",
                    tool_name="generate_image",
                    execution_time_ms=2000,
                    status="success",
                    meta_info={"image_usage": _usage(requested=1, succeeded=1, total_tokens=50, elapsed_ms=2000)},
                    created_at=stamp,
                ),
                # 普通工具调用：没有 image_usage，不能被算成一次图像调用
                AgentExecutionTrace(
                    trace_id=f"{TRACE_PREFIX}own-3",
                    step_number=8,
                    event_type="tool_call",
                    tool_name="write_file",
                    execution_time_ms=300,
                    status="success",
                    meta_info={"timeout_seconds": 180},
                    created_at=stamp,
                ),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_summary_counts_images_and_upstream_tokens(client: AsyncClient, admin_api_key: str):
    """汇总：张数、请求张数、失败张数、调用次数、上游 token 各自独立计价。"""
    await _cleanup()
    await _seed()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage",
            params=_range(),
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        summary = response.json()["summary"]

        # 2/2 + 1/2 + 1/1 → 出图 4 张，请求 5 张，失败 1 张
        assert summary["images"] == 4
        assert summary["requested"] == 5
        assert summary["failed"] == 1
        # write_file 那条没有 image_usage，不该被算成一次图像调用
        assert summary["calls"] == 3
        assert summary["upstream_tokens"] == 150
        # 上游没返回 usage 的那次单独计数，不能摊进 0 装作「没花钱」
        assert summary["tokens_missing_calls"] == 1
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_non_admin_sees_only_own_usage(client: AsyncClient, valid_api_key: str):
    """轨迹表没有 username，靠 trace_id 关联历史表；关联不上的不能露给普通用户。"""
    await _cleanup()
    await _seed()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage",
            params=_range(),
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200, response.text
        summary = response.json()["summary"]

        assert summary["images"] == 3, "只应包含 test_user 自己的 2+1 张"
        assert summary["requested"] == 4
        assert summary["calls"] == 2, "test_admin 的那次调用不能混进来"
        assert summary["upstream_tokens"] == 100
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_groups_by_agent(client: AsyncClient, admin_api_key: str):
    """按智能体分组：同一智能体的多次调用要合并成一行，并保留最近一次时间。"""
    await _cleanup()
    await _seed()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage",
            params={**_range(), "group_by": "agent"},
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        groups = {row["key"]: row for row in response.json()["groups"]}

        assert AGENT_OWNER in groups, "按智能体分组应出现本次种子的智能体"
        owner = groups[AGENT_OWNER]
        assert owner["images"] == 3
        assert owner["requested"] == 4
        assert owner["failed"] == 1
        assert owner["calls"] == 2
        assert owner["upstream_tokens"] == 100
        assert owner["tokens_missing_calls"] == 1
        assert owner["avg_elapsed_ms"] == 2000, "(1000 + 3000) / 2"
        assert owner["last_at"], "分组行要带最近一次调用时间"

        assert groups[AGENT_OTHER]["images"] == 1
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_groups_by_user_requires_admin(client: AsyncClient, valid_api_key: str):
    """按用户分组只有管理员有意义：普通用户只有自己一个维度，不能静默降级。"""
    response = await client.get(
        "/api/portal/dashboard/token-stats/image-usage",
        params={**_range(), "group_by": "user"},
        headers={"X-API-Key": valid_api_key},
    )
    assert response.status_code == 400, "非管理员请求按用户分组应明确拒绝"


@pytest.mark.asyncio
async def test_trends_are_filled_for_every_day_in_range(client: AsyncClient, admin_api_key: str):
    """趋势要按天补齐空档：缺的天返回 0，否则前端画图会把 3 天画成 1 天。"""
    await _cleanup()
    await _seed()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage",
            params=_range(),
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        trends = response.json()["trends"]

        assert len(trends) == 3, "11-25 ~ 11-27 应补齐 3 天"
        assert sum(row["images"] for row in trends) == 4
        assert all("date" in row for row in trends)
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_invalid_range_is_rejected(client: AsyncClient, admin_api_key: str):
    """半开区间不能让用户以为「区间生效了」——静默回落会掩盖调用错误。"""
    response = await client.get(
        "/api/portal/dashboard/token-stats/image-usage",
        params={"start_date": _day(2)},
        headers={"X-API-Key": admin_api_key},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_export_returns_csv_with_header(client: AsyncClient, admin_api_key: str):
    """导出与页面必须同源：CSV 走同一套聚合，否则页面和账单对不上。"""
    await _cleanup()
    await _seed()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage/export",
            params={**_range(), "group_by": "agent"},
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        assert "text/csv" in response.headers["content-type"]
        assert "attachment" in response.headers.get("content-disposition", "")

        text = response.text
        header = text.splitlines()[0]
        for column in ("出图张数", "请求张数", "上游Token", "调用次数"):
            assert column in header, f"CSV 表头缺少 {column}：{header}"
        assert str(AGENT_OWNER) in text, "导出内容应包含聚合行"
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_legacy_rows_are_recovered_from_tool_output(
    client: AsyncClient, admin_api_key: str
):
    """修复前的历史轨迹：`meta_info` 没有用量，但 `tool_output.raw` 里留着完整一份。

    2026-11-23 之前 `generate_image` 走的是工具包装层的非流式返回路径——用量没被摘进
    metadata，整份结果（含 `image_usage`）被 json 序列化进了 `tool_output.raw`。
    不兜这条，重启后统计页对那些已经花过钱的调用仍然显示 0，用户会以为没修好。
    """
    await _cleanup()
    stamp = datetime.now() + timedelta(days=3)
    legacy_usage = _usage(requested=2, succeeded=2, total_tokens=70)
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AgentExecutionHistory(
                    agent_id=AGENT_OWNER,
                    trace_id=f"{TRACE_PREFIX}legacy-1",
                    username=OWNER,
                    status="success",
                    created_at=stamp,
                ),
                AgentExecutionTrace(
                    trace_id=f"{TRACE_PREFIX}legacy-1",
                    step_number=3,
                    event_type="tool_call",
                    tool_name="generate_image",
                    execution_time_ms=6500,
                    status="success",
                    # 只有超时信息，没有 image_usage
                    meta_info={"timeout_source": "tool_defined", "timeout_seconds": 300.0},
                    tool_output={
                        "raw": json.dumps(
                            {
                                "ok": True,
                                "images": [{"filename": "generated-image-1.png"}],
                                "image_usage": legacy_usage,
                                "hint": "用 markdown 引用 download_url",
                            },
                            ensure_ascii=False,
                        )
                    },
                    created_at=stamp,
                ),
            ]
        )
        await session.commit()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage",
            params=_range(),
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        summary = response.json()["summary"]

        assert summary["images"] == 2
        assert summary["calls"] == 1
        assert summary["upstream_tokens"] == 70
    finally:
        await _cleanup()


@pytest.mark.asyncio
async def test_legacy_recovery_does_not_double_count(client: AsyncClient, admin_api_key: str):
    """`meta_info` 与 `tool_output` 都有用量时只算一次（不能因为兜底翻倍）。"""
    await _cleanup()
    stamp = datetime.now() + timedelta(days=3)
    usage = _usage(requested=1, succeeded=1, total_tokens=30)
    async with AsyncSessionLocal() as session:
        session.add_all(
            [
                AgentExecutionHistory(
                    agent_id=AGENT_OWNER,
                    trace_id=f"{TRACE_PREFIX}both-1",
                    username=OWNER,
                    status="success",
                    created_at=stamp,
                ),
                AgentExecutionTrace(
                    trace_id=f"{TRACE_PREFIX}both-1",
                    step_number=3,
                    event_type="tool_call",
                    tool_name="generate_image",
                    execution_time_ms=1000,
                    status="success",
                    meta_info={"image_usage": usage},
                    tool_output={"raw": json.dumps({"ok": True, "image_usage": usage})},
                    created_at=stamp,
                ),
            ]
        )
        await session.commit()
    try:
        response = await client.get(
            "/api/portal/dashboard/token-stats/image-usage",
            params=_range(),
            headers={"X-API-Key": admin_api_key},
        )
        assert response.status_code == 200, response.text
        summary = response.json()["summary"]

        assert summary["calls"] == 1, "同一次调用只能算一次"
        assert summary["images"] == 1
        assert summary["upstream_tokens"] == 30
    finally:
        await _cleanup()
