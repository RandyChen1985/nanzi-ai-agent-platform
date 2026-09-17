import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_access_log_recording(client: AsyncClient, valid_api_key: str, wait_for_access_log):
    """验证请求会被记录到 MySQL ai_agent_access_logs 表"""

    # 1. 发起请求
    response = await client.get(
        "/api/portal/auth/me",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200

    # 验证响应头中包含 X-Trace-Id
    assert "X-Trace-Id" in response.headers
    trace_id = response.headers["X-Trace-Id"]

    # 2. 等待审计日志落库
    #    日志由响应的 BackgroundTask 在响应返回之后才入队，而 flush() 只排空进程内队列，
    #    因此必须轮询等待，否则会随机读到"尚未写入"。
    row = await wait_for_access_log(trace_id)
    assert row is not None
    assert row["status_code"] == 200
    # user_name should be present if authenticated
    assert row["user_name"] is not None


@pytest.mark.asyncio
async def test_access_log_unauthorized(client: AsyncClient, wait_for_access_log):
    """验证未授权请求也会记录（user_id 为空）"""
    response = await client.get("/api/portal/auth/me")
    assert response.status_code == 401

    trace_id = response.headers.get("X-Trace-Id")
    assert trace_id is not None

    # 2. 等待审计日志落库（同上，由 BackgroundTask 异步入队）
    row = await wait_for_access_log(trace_id)
    assert row is not None
    assert row["status_code"] == 401
