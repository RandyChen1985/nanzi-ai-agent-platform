"""批量改密：统一密码 + 逐用户等保校验。

等保要求里有一条是「密码不能包含用户名」，而批量改密用的是**同一个**密码：
密码里出现 `alice` 对 alice 不合规、对 bob 未必不合规。所以判定必须逐用户做，
撞上的那个单独失败并点名，其余照常改密——不能让一个"名字像密码片段"的账号
把整批 15 个人拖死。

另外两条回归重点：
- 改密后必须逐个吊销会话，否则「管理员发现被盗 → 批量改密」等于没改；
- 必须跳过当前登录账号，否则管理员会把自己踢下线。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.management import (
    BATCH_STATUS_MAX_USERS,
    BatchSetPasswordRequest,
    batch_set_user_password,
)
from app.services.auth_service import AuthService

pytestmark = pytest.mark.no_infrastructure

ADMIN = {"user_id": "1", "user_name": "admin", "role": "admin"}
# 16 位、四类字符齐全，且不含任何测试用户名
STRONG_PASSWORD = "Secr3t!Passw0rd"


def _db_with(users):
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(users))
        )
    )
    return db


def _user(uid, name):
    return SimpleNamespace(id=uid, user_name=name)


@pytest.mark.asyncio
async def test_batch_password_applies_to_every_selected_user():
    """统一密码逐用户落库，并逐个吊销会话。"""
    users = [_user(2, "alice"), _user(3, "bob")]
    db = _db_with(users)

    with patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ) as set_pwd, patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ) as revoke, patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_set_user_password(
            request=BatchSetPasswordRequest(user_ids=[2, 3], password=STRONG_PASSWORD),
            admin=ADMIN,
            db=db,
        )

    assert result["updated"] == 2
    assert result["updated_ids"] == [2, 3]
    assert result["failed"] == []
    assert result["skipped_self"] == 0
    assert result["not_found"] == 0
    assert set_pwd.await_count == 2
    assert revoke.await_count == 2
    # 密码原样传递，不能被 bcrypt 72 字节截断逻辑改坏
    assert set_pwd.await_args_list[0].args[1] == STRONG_PASSWORD


@pytest.mark.asyncio
async def test_user_whose_name_is_inside_password_fails_alone():
    """密码包含某用户用户名时，只有该用户失败并给出原因，其余照常。"""
    users = [_user(2, "Secr3t"), _user(3, "bob")]
    db = _db_with(users)

    with patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ), patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ), patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_set_user_password(
            request=BatchSetPasswordRequest(user_ids=[2, 3], password=STRONG_PASSWORD),
            admin=ADMIN,
            db=db,
        )

    assert result["updated"] == 1
    assert result["updated_ids"] == [3]
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["user_id"] == 2
    assert failed["user_name"] == "Secr3t"
    assert "用户名" in failed["reason"]


@pytest.mark.asyncio
async def test_current_admin_is_skipped_not_reset():
    """勾选里含当前登录账号时跳过它，避免管理员把自己踢下线。"""
    users = [_user(2, "alice")]
    db = _db_with(users)

    with patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ), patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ), patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_set_user_password(
            request=BatchSetPasswordRequest(user_ids=[1, 2], password=STRONG_PASSWORD),
            admin=ADMIN,
            db=db,
        )

    assert result["skipped_self"] == 1
    assert result["updated_ids"] == [2]


@pytest.mark.asyncio
async def test_weak_password_rejected_before_touching_database():
    """通用合规校验先于查库：不合规直接 400，不做任何写入。"""
    db = AsyncMock()

    with patch.object(AuthService, "set_user_password", new_callable=AsyncMock) as set_pwd:
        with pytest.raises(HTTPException) as excinfo:
            await batch_set_user_password(
                request=BatchSetPasswordRequest(
                    user_ids=[2, 3], password="abcdefgh"  # 仅小写，1 类字符
                ),
                admin=ADMIN,
                db=db,
            )

    assert excinfo.value.status_code == 400
    assert "等保" in excinfo.value.detail
    db.execute.assert_not_called()
    set_pwd.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_and_oversized_target_lists_rejected():
    db = AsyncMock()

    with pytest.raises(HTTPException) as empty_case:
        await batch_set_user_password(
            request=BatchSetPasswordRequest(user_ids=[], password=STRONG_PASSWORD),
            admin=ADMIN,
            db=db,
        )
    assert empty_case.value.status_code == 400

    with pytest.raises(HTTPException) as too_many:
        await batch_set_user_password(
            request=BatchSetPasswordRequest(
                user_ids=list(range(BATCH_STATUS_MAX_USERS + 1)),
                password=STRONG_PASSWORD,
            ),
            admin=ADMIN,
            db=db,
        )
    assert too_many.value.status_code == 400
    assert str(BATCH_STATUS_MAX_USERS) in too_many.value.detail


@pytest.mark.asyncio
async def test_missing_users_are_counted_not_silently_dropped():
    """库中不存在的 ID 要计入 not_found，前端才能如实告知。"""
    db = _db_with([_user(2, "alice")])

    with patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ), patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ), patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_set_user_password(
            request=BatchSetPasswordRequest(user_ids=[2, 99], password=STRONG_PASSWORD),
            admin=ADMIN,
            db=db,
        )

    assert result["updated"] == 1
    assert result["not_found"] == 1
