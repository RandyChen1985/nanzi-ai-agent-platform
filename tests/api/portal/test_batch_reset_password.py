"""批量重置密码（邮件下发随机密码）。

这组用例守的是三个"顺序"：

1. **先发信、后落库**。发信失败必须保持原密码不变——否则 SMTP 抖一下就会留下
   「密码已改、邮件没收到」的账号，用户不知道该输什么，管理员也不知道。
2. **先判配置、后查库**。平台邮件服务没启用时直接 400，不做任何写入。
3. **没邮箱跳过、别让整批失败**。一个人没填邮箱不该拖累另外 14 个人。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.portal.endpoints.management import (
    BatchResetPasswordRequest,
    batch_reset_user_password,
)
from app.services.auth_service import AuthService

pytestmark = pytest.mark.no_infrastructure

ADMIN = {"user_id": "1", "user_name": "admin", "role": "admin"}
SMTP = SimpleNamespace(sender_name="AI 智能体平台")


def _user(uid, name, email=None):
    return SimpleNamespace(id=uid, user_name=name, email=email)


def _db_with(users):
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: list(users))
        )
    )
    return db


def _patched_smtp():
    return patch(
        "app.services.email_delivery_service.EmailDeliveryService.load_global_smtp_settings",
        new_callable=AsyncMock,
        return_value=SMTP,
    )


@pytest.mark.asyncio
async def test_reset_generates_distinct_compliant_passwords_and_mails_them():
    """每个用户拿到不同的随机密码，且都过等保校验、都落了库、都吊销了会话。"""
    users = [_user(2, "alice", "alice@example.com"), _user(3, "bob", "bob@example.com")]
    db = _db_with(users)

    with _patched_smtp(), patch(
        "app.api.portal.endpoints.management._send_password_reset_mail",
        new_callable=AsyncMock,
        return_value=(True, None),
    ) as send_mail, patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ) as set_pwd, patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ) as revoke, patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_reset_user_password(
            request=BatchResetPasswordRequest(user_ids=[2, 3]),
            admin=ADMIN,
            db=db,
        )

    assert result["reset"] == 2
    assert result["failed"] == []
    assert result["skipped_no_email"] == []
    assert send_mail.await_count == 2
    assert revoke.await_count == 2

    passwords = [call.args[1] for call in set_pwd.await_args_list]
    assert len(set(passwords)) == 2, "每位用户必须拿到各自独立的随机密码"
    for password in passwords:
        ok, _ = AuthService.validate_password_complexity(password)
        assert ok, f"下发的密码必须本身合规：{password}"

    # 邮件里发的密码必须就是落库的那一个，不能各算一次
    mailed = [call.args[1] for call in send_mail.await_args_list]
    assert mailed == passwords


@pytest.mark.asyncio
async def test_users_without_email_are_skipped_not_failed():
    """没填邮箱的用户跳过并点名，其余照常重置。"""
    users = [
        _user(2, "nomail", None),
        _user(3, "bob", "  "),  # 空白也算没填
        _user(4, "carol", "carol@example.com"),
    ]
    db = _db_with(users)

    with _patched_smtp(), patch(
        "app.api.portal.endpoints.management._send_password_reset_mail",
        new_callable=AsyncMock,
        return_value=(True, None),
    ), patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ), patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ), patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_reset_user_password(
            request=BatchResetPasswordRequest(user_ids=[2, 3, 4]),
            admin=ADMIN,
            db=db,
        )

    assert result["reset"] == 1
    assert result["reset_ids"] == [4]
    assert [item["user_name"] for item in result["skipped_no_email"]] == ["nomail", "bob"]
    assert result["failed"] == []


@pytest.mark.asyncio
async def test_mail_failure_leaves_original_password_untouched():
    """发信失败 → 不写库（先发信后落库），计入 failed 并给出原因。"""
    users = [_user(2, "alice", "alice@example.com")]
    db = _db_with(users)

    with _patched_smtp(), patch(
        "app.api.portal.endpoints.management._send_password_reset_mail",
        new_callable=AsyncMock,
        return_value=(False, "SMTP 530 认证失败"),
    ), patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ) as set_pwd, patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ) as revoke:
        result = await batch_reset_user_password(
            request=BatchResetPasswordRequest(user_ids=[2]),
            admin=ADMIN,
            db=db,
        )

    assert result["reset"] == 0
    assert len(result["failed"]) == 1
    assert "SMTP" in result["failed"][0]["reason"]
    set_pwd.assert_not_awaited()
    revoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_mail_service_disabled_rejects_before_touching_database():
    """平台邮件服务未启用：直接 400，不查库、不发信、不写库。"""
    db = AsyncMock()

    with patch(
        "app.services.email_delivery_service.EmailDeliveryService.load_global_smtp_settings",
        new_callable=AsyncMock,
        return_value=None,
    ), patch(
        "app.api.portal.endpoints.management._send_password_reset_mail",
        new_callable=AsyncMock,
    ) as send_mail:
        with pytest.raises(HTTPException) as excinfo:
            await batch_reset_user_password(
                request=BatchResetPasswordRequest(user_ids=[2]),
                admin=ADMIN,
                db=db,
            )

    assert excinfo.value.status_code == 400
    assert "邮件服务" in excinfo.value.detail
    db.execute.assert_not_called()
    send_mail.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_admin_skipped_and_empty_list_rejected():
    users = [_user(2, "alice", "alice@example.com")]
    db = _db_with(users)

    with _patched_smtp(), patch(
        "app.api.portal.endpoints.management._send_password_reset_mail",
        new_callable=AsyncMock,
        return_value=(True, None),
    ), patch.object(
        AuthService, "set_user_password", new_callable=AsyncMock, return_value=True
    ), patch.object(
        AuthService, "revoke_sessions_for_user", new_callable=AsyncMock
    ), patch(
        "app.api.portal.endpoints.management.PermissionService"
    ) as permission_cls:
        permission_cls.return_value.invalidate_cached_permissions_for_users = AsyncMock()
        result = await batch_reset_user_password(
            request=BatchResetPasswordRequest(user_ids=[1, 2]),
            admin=ADMIN,
            db=db,
        )

    assert result["skipped_self"] == 1
    assert result["reset_ids"] == [2]

    db2 = AsyncMock()
    with pytest.raises(HTTPException) as excinfo:
        await batch_reset_user_password(
            request=BatchResetPasswordRequest(user_ids=[]),
            admin=ADMIN,
            db=db2,
        )
    assert excinfo.value.status_code == 400


def test_generated_password_always_passes_policy():
    """生成器与校验器是同一条规则的两端：100 次抽样必须全部合规。"""
    for _ in range(100):
        password = AuthService.generate_compliant_password(username="alice")
        assert len(password) == 16
        ok, _ = AuthService.validate_password_complexity(password, username="alice")
        assert ok
        # 易混字符不该出现在要用户手抄的密码里
        assert not set(password) & set("Il1O0")
