from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.quota_service import QuotaService


def _policy(enabled=True, limit_tokens=1000):
    policy = MagicMock()
    policy.enabled = enabled
    policy.limit_tokens = limit_tokens
    policy.action_on_exceed = "block"
    return policy


@pytest.mark.asyncio
async def test_check_before_call_blocks_when_exceeded():
    db = AsyncMock()
    service = QuotaService(db)
    service.get_user_quota_status = AsyncMock(
        return_value=MagicMock(
            is_admin_bypass=False,
            limit_tokens=1000,
            used_tokens=1200,
        )
    )

    message = await service.check_before_call({"user_id": 1, "user_name": "alice"})
    assert message is not None
    assert "额度已用尽" in message


@pytest.mark.asyncio
async def test_check_before_call_allows_unlimited():
    db = AsyncMock()
    service = QuotaService(db)
    service.get_user_quota_status = AsyncMock(
        return_value=MagicMock(
            is_admin_bypass=False,
            limit_tokens=None,
            used_tokens=999999,
        )
    )

    message = await service.check_before_call({"user_id": 1, "user_name": "alice"})
    assert message is None


@pytest.mark.asyncio
async def test_resolve_effective_limit_prefers_user_override():
    db = AsyncMock()
    service = QuotaService(db)

    user = MagicMock()
    user.role = "user"
    db.get = AsyncMock(return_value=user)

    async def fake_get_policy(scope_type, scope_id):
        if scope_type == "user":
            return _policy(enabled=True, limit_tokens=5000)
        return None

    service._get_policy = fake_get_policy  # type: ignore[method-assign]
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: []))

    limit, source, _ = await service._resolve_effective_limit(1, "alice")
    assert limit == 5000
    assert source == "user"


@pytest.mark.asyncio
async def test_build_warning_message_near_limit():
    db = AsyncMock()
    service = QuotaService(db)
    status = service._build_status(
        used_tokens=850_000,
        limit_tokens=1_000_000,
        source="role",
        source_label="角色：分析师",
        is_admin_bypass=False,
    )
    message = service.build_warning_message(status)
    assert message is not None
    assert "即将用尽" in message


@pytest.mark.asyncio
async def test_resolve_effective_limit_batches_role_policies():
    db = AsyncMock()
    service = QuotaService(db)

    user = MagicMock()
    user.role = "user"
    db.get = AsyncMock(return_value=user)
    db.execute = AsyncMock(
        return_value=MagicMock(all=lambda: [(1, "分析师"), (2, "运营")])
    )

    async def fake_batch(scope_type, scope_ids):
        assert scope_type == "role"
        assert scope_ids == [1, 2]
        p1 = _policy(enabled=True, limit_tokens=200000)
        p1.scope_id = 1
        p2 = _policy(enabled=True, limit_tokens=500000)
        p2.scope_id = 2
        return {1: p1, 2: p2}

    service._get_policies_for_scope_ids = fake_batch  # type: ignore[method-assign]
    # 实现新增了「用户专属政策优先」这一层：不显式声明「没有用户专属政策」，没被 mock 的
    # _get_policy 会返回 MagicMock（truthy 且 enabled），而 int(MagicMock) 恰好等于 1，
    # 断言就会退化成 1 == 500000。
    service._get_policy = AsyncMock(return_value=None)  # type: ignore[method-assign]

    limit, source, label = await service._resolve_effective_limit(1, "alice")
    assert limit == 500000
    assert source == "role"
    assert label == "角色：运营"
