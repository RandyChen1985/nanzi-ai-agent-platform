"""
Tests for Dashboard API endpoints
"""
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.core.orm import AsyncSessionLocal
from app.models.audit import AccessLog
from app.models.user import User


@pytest.mark.asyncio
async def test_admin_stats_default(client: AsyncClient, admin_api_key: str):
    """Test admin stats with default period (today)"""
    response = await client.get(
        "/api/portal/dashboard/admin-stats",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Check required fields
    assert "total_users" in data
    assert "active_users" in data
    assert "api_calls" in data
    assert "avg_response_time" in data
    assert "success_rate" in data
    assert "error_rate" in data
    
    # Check api_calls structure
    assert data["api_calls"]["period"] == "today"
    assert "total" in data["api_calls"]
    assert "success" in data["api_calls"]
    assert "errors" in data["api_calls"]


@pytest.mark.asyncio
async def test_admin_stats_accessible_for_user(client: AsyncClient, valid_api_key: str):
    """Test that regular users can access stats (but see only their own data)"""
    response = await client.get(
        "/api/portal/dashboard/admin-stats",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Regular users should see api_calls but not total_users/active_users
    assert "api_calls" in data
    assert "avg_response_time" in data
    assert "success_rate" in data
    assert "error_rate" in data
    
    # Admin-only fields should NOT be present for regular users
    assert "total_users" not in data
    assert "active_users" not in data


@pytest.mark.asyncio
async def test_user_stats_default(client: AsyncClient, valid_api_key: str):
    """Test user stats with default period"""
    response = await client.get(
        "/api/portal/dashboard/user-stats",
        headers={"X-API-Key": valid_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Check required fields
    assert "api_key_status" in data
    assert "api_calls" in data
    assert "avg_response_time" in data
    assert "success_rate" in data
    assert "last_call_time" in data
    
    # Check API key status
    assert data["api_key_status"] in ["active", "inactive"]
    
    # Check api_calls structure
    assert data["api_calls"]["period"] == "today"


@pytest.mark.asyncio
async def test_api_trends_default(client: AsyncClient, admin_api_key: str):
    """Test API trends with default days (7)"""
    response = await client.get(
        "/api/portal/dashboard/api-trends",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Should return an array
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_recent_activities_admin(client: AsyncClient, admin_api_key: str):
    """Test recent activities for admin"""
    response = await client.get(
        "/api/portal/dashboard/recent-activities",
        headers={"X-API-Key": admin_api_key}
    )
    assert response.status_code == 200
    data = response.json()
    
    # Admins should see all sections
    assert "recent_users" in data
    assert "recent_calls" in data
    assert "recent_errors" in data


@pytest.mark.asyncio
async def test_recent_users_are_sorted_by_latest_activity_not_registration_time(
    client: AsyncClient,
    admin_api_key: str,
    db_session,
):
    now = datetime.now()
    suffix = uuid4().hex[:8]
    older_activity_user = f"created-later-{suffix}"
    newer_activity_user = f"active-later-{suffix}"

    # recent-users 硬限 Top 5（接口里是 .limit(min(limit, 5))），共享库中其它测试
    # 留下的更近活动会把本用例的两个用户挤出列表。把活动时间整体推到"未来"，
    # 既保证进入 Top 5，又保持"注册更晚但活动更早"这一被测关系的方向不变。
    activity_base = now + timedelta(days=1)

    # 先清掉历次运行残留的同前缀数据：这些记录也带"未来活动"时间，会随每次
    # 运行前移，逐渐把 Top 5 占满并让本用例变得越来越红。
    async with AsyncSessionLocal() as pre_clean:
        for _prefix in ("active-later-%", "created-later-%"):
            await pre_clean.execute(
                delete(AccessLog).where(AccessLog.user_name.like(_prefix))
            )
            await pre_clean.execute(delete(User).where(User.user_name.like(_prefix)))
        await pre_clean.commit()

    db_session.add_all([
        User(
            user_name=older_activity_user,
            api_key_hash=f"hash-older-{suffix}",
            api_key_encrypted=f"encrypted-older-{suffix}",
            role="user",
            status=1,
            created_at=now + timedelta(minutes=2),
        ),
        User(
            user_name=newer_activity_user,
            api_key_hash=f"hash-newer-{suffix}",
            api_key_encrypted=f"encrypted-newer-{suffix}",
            role="user",
            status=1,
            created_at=now,
        ),
        AccessLog(
            user_name=older_activity_user,
            trace_id=f"trace-older-{suffix}",
            endpoint="/older",
            method="GET",
            status_code=200,
            process_time_ms=1.0,
            created_at=activity_base - timedelta(minutes=2),
        ),
        AccessLog(
            user_name=newer_activity_user,
            trace_id=f"trace-newer-{suffix}",
            endpoint="/newer",
            method="GET",
            status_code=200,
            process_time_ms=1.0,
            created_at=activity_base - timedelta(minutes=1),
        ),
    ])
    await db_session.commit()

    response = await client.get(
        "/api/portal/dashboard/recent-activities",
        headers={"X-API-Key": admin_api_key},
        params={"limit": 50},
    )

    assert response.status_code == 200
    users = response.json()["recent_users"]
    names = [item["user_name"] for item in users]

    # 本用例写入的是共享库里的持久数据，必须自己清理：否则每跑一次就多留一条
    # "未来活动"记录，recent-users 的 Top 5 会被历次残留占满，测试逐渐变红。
    try:
        assert names.index(newer_activity_user) < names.index(older_activity_user)
        activity_item = next(
            item for item in users if item["user_name"] == newer_activity_user
        )
        assert activity_item["last_active"] is not None
    finally:
        async with AsyncSessionLocal() as cleanup:
            await cleanup.execute(
                delete(AccessLog).where(
                    AccessLog.user_name.in_([older_activity_user, newer_activity_user])
                )
            )
            await cleanup.execute(
                delete(User).where(
                    User.user_name.in_([older_activity_user, newer_activity_user])
                )
            )
            await cleanup.commit()
