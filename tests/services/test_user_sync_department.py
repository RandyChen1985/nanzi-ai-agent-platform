"""第三方用户同步的部门归属：可选开关、单列映射、默认只增不减。

第三方 HR 表通常带部门编码，但本平台的部门树是自己维护的：
**同步只负责「把人挂到已有部门上」**，不在 undefined 的父级上凭空造部门。
因此这里锁住四件事：

1. 配置默认关闭，旧配置（没有 dept_sync 键）仍能解析；
2. 只有启用时才把来源部门列选进 SQL，避免无谓的列依赖；
3. 来源 code 在本平台找不到时跳过并计数，不静默丢人；
4. 写入必须经 DepartmentService——主部门快照与 is_primary 唯一性由它保证。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.user_sync import (
    ThirdPartyUserSyncConfig,
    ThirdPartyUserSyncDeptSync,
    ThirdPartyUserSyncFieldMap,
)
from app.services.user_sync_service import UserSyncService


def _config(**dept_kwargs) -> ThirdPartyUserSyncConfig:
    return ThirdPartyUserSyncConfig(
        enabled=True,
        connection_config_id=1,
        table_name="hr_users",
        field_map=ThirdPartyUserSyncFieldMap(user_name="login_name"),
        dept_sync=ThirdPartyUserSyncDeptSync(enabled=True, source_column="dept_code", **dept_kwargs),
    )


def test_dept_sync_defaults_to_off_and_additive():
    cfg = ThirdPartyUserSyncConfig()
    assert cfg.dept_sync.enabled is False
    assert cfg.dept_sync.source_column is None
    assert cfg.dept_sync.mode == "add"


def test_old_config_without_dept_sync_still_parses():
    """配置存的是 JSON，历史配置里没有 dept_sync 键，升级后不能因此报错。"""
    cfg = ThirdPartyUserSyncConfig(**{
        "enabled": True,
        "connection_config_id": 3,
        "table_name": "hr_users",
        "field_map": {"user_name": "login_name"},
    })
    assert cfg.dept_sync.enabled is False


def test_select_sql_includes_dept_column_only_when_enabled():
    field_map = ThirdPartyUserSyncFieldMap(user_name="login_name")

    enabled = UserSyncService._build_select_sql(
        "hr_users", field_map, "mysql",
        dept_sync=ThirdPartyUserSyncDeptSync(enabled=True, source_column="dept_code"),
    )
    assert "`dept_code` AS dept_code" in enabled

    disabled = UserSyncService._build_select_sql("hr_users", field_map, "mysql")
    assert "AS dept_code" not in disabled


def test_normalize_external_user_extracts_and_strips_dept_code():
    result = UserSyncService._normalize_external_user(
        {"user_name": "alice", "dept_code": "  dev  "}
    )
    assert result["dept_code"] == "dev"

    blank = UserSyncService._normalize_external_user({"user_name": "alice", "dept_code": "   "})
    assert blank["dept_code"] is None


@pytest.mark.asyncio
async def test_run_sync_links_existing_department_in_add_mode():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(all=MagicMock(return_value=[("dev", 7)])),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        MagicMock(scalar=MagicMock(return_value=42)),
    ])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    external = [{
        "user_name": "alice", "real_name": None, "remark": None,
        "extra_data": None, "dept_code": "dev",
    }]

    with patch.object(UserSyncService, "fetch_external_users", AsyncMock(return_value=external)), \
         patch("app.services.user_sync_service.AuthService.generate_api_key", AsyncMock()), \
         patch.object(UserSyncService, "_link_departments", AsyncMock()) as link:
        result = await UserSyncService.run_sync(db, config=_config())

    assert result == {
        "created": 1, "updated": 0, "failed": 0,
        "dept_linked": 1, "dept_skipped": 0,
    }
    link.assert_awaited_once_with(db, 42, 7, "add")


@pytest.mark.asyncio
async def test_run_sync_counts_unmatched_department_and_keeps_going():
    """平台没有这个部门 code：跳过该人的部门归属，但账号照常创建，不能整批失败。"""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
    ])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    external = [{
        "user_name": "alice", "real_name": None, "remark": None,
        "extra_data": None, "dept_code": "ghost",
    }]

    with patch.object(UserSyncService, "fetch_external_users", AsyncMock(return_value=external)), \
         patch("app.services.user_sync_service.AuthService.generate_api_key", AsyncMock()), \
         patch.object(UserSyncService, "_link_departments", AsyncMock()) as link:
        result = await UserSyncService.run_sync(db, config=_config())

    assert result["created"] == 1
    assert result["dept_skipped"] == 1
    assert result["dept_linked"] == 0
    link.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_sync_replace_mode_is_passed_through():
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        MagicMock(all=MagicMock(return_value=[("ops", 3)])),
        MagicMock(scalar_one_or_none=MagicMock(return_value=SimpleNamespace(id=11, user_name="alice"))),
    ])
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    external = [{
        "user_name": "alice", "real_name": None, "remark": None,
        "extra_data": None, "dept_code": "ops",
    }]

    with patch.object(UserSyncService, "fetch_external_users", AsyncMock(return_value=external)), \
         patch.object(UserSyncService, "_link_departments", AsyncMock()) as link:
        result = await UserSyncService.run_sync(db, config=_config(mode="replace"))

    assert result["dept_linked"] == 1
    link.assert_awaited_once_with(db, 11, 3, "replace")


@pytest.mark.asyncio
async def test_link_departments_add_mode_uses_add_members():
    """只增不减：add_members 幂等，且不会动用户已有的主部门。"""
    with patch("app.services.department_service.DepartmentService") as svc_cls:
        svc = svc_cls.return_value
        svc.add_members = AsyncMock()
        await UserSyncService._link_departments(AsyncMock(), 5, 7, "add")

    svc.add_members.assert_awaited_once_with(7, [5])
    svc.set_user_departments.assert_not_called()


@pytest.mark.asyncio
async def test_link_departments_replace_mode_replaces_membership():
    """以来源为准：整集替换成来源部门，会移除手工加的其它部门。"""
    with patch("app.services.department_service.DepartmentService") as svc_cls:
        svc = svc_cls.return_value
        svc.set_user_departments = AsyncMock()
        await UserSyncService._link_departments(AsyncMock(), 5, 7, "replace")

    svc.set_user_departments.assert_awaited_once_with(5, [7])
    svc.add_members.assert_not_called()
