import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.user_sync import (
    ThirdPartyUserSyncConfig,
    ThirdPartyUserSyncExtraDataMapping,
    ThirdPartyUserSyncFieldMap,
)
from app.services.user_sync_service import UserSyncService


def test_build_select_sql_mysql():
    field_map = ThirdPartyUserSyncFieldMap(
        user_name="login_name",
        real_name="display_name",
        remark="dept",
    )
    sql = UserSyncService._build_select_sql("hr_users", field_map, "mysql")
    assert "FROM `hr_users`" in sql
    assert "`login_name` AS user_name" in sql


def test_build_select_sql_with_extra_data_mappings():
    field_map = ThirdPartyUserSyncFieldMap(user_name="login_name")
    mappings = [
        ThirdPartyUserSyncExtraDataMapping(json_key="id", source_column="user_id"),
        ThirdPartyUserSyncExtraDataMapping(json_key="dept", source_column="dept_code"),
    ]
    sql = UserSyncService._build_select_sql("hr_users", field_map, "mysql", mappings)
    assert "`user_id` AS __extra__id" in sql
    assert "`dept_code` AS __extra__dept" in sql


def test_build_extra_data_json():
    row = {
        "user_name": "alice",
        "__extra__id": 100,
        "__extra__dept": "RD",
    }
    mappings = [
        ThirdPartyUserSyncExtraDataMapping(json_key="id", source_column="user_id"),
        ThirdPartyUserSyncExtraDataMapping(json_key="dept", source_column="dept_code"),
    ]
    payload = UserSyncService._build_extra_data_json(row, mappings)
    assert payload == '{"id": 100, "dept": "RD"}'


def test_normalize_external_user():
    row = {"user_name": " alice ", "real_name": "Bob", "remark": None}
    result = UserSyncService._normalize_external_user(row)
    assert result == {
        "user_name": "alice",
        "real_name": "Bob",
        "remark": None,
        "extra_data": None,
    }


def test_normalize_external_user_requires_username():
    assert UserSyncService._normalize_external_user({"user_name": "  "}) is None
    assert UserSyncService._normalize_external_user({}) is None


def test_format_sync_remark():
    assert UserSyncService._format_sync_remark(None) == "第三方同步"
    assert UserSyncService._format_sync_remark("研发部") == "第三方同步: 研发部"
    assert UserSyncService._format_sync_remark("第三方同步: 已有") == "第三方同步: 已有"


def test_schedule_to_cron():
    assert UserSyncService.schedule_to_cron("hourly") == {"minute": 0}
    assert UserSyncService.schedule_to_cron("daily") == {"hour": 2, "minute": 0}
    assert UserSyncService.schedule_to_cron("off") is None


def test_default_config_roundtrip():
    cfg = UserSyncService._default_config()
    assert cfg.enabled is False
    assert cfg.schedule == "off"
    assert cfg.field_map.user_name == ""


@pytest.mark.asyncio
async def test_run_sync_creates_new_user_by_username():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    config = ThirdPartyUserSyncConfig(
        enabled=True,
        connection_config_id=1,
        table_name="hr_users",
        field_map=ThirdPartyUserSyncFieldMap(user_name="login_name"),
    )
    external = [{"user_name": "alice", "real_name": "Alice", "remark": "研发", "extra_data": None}]

    with patch.object(UserSyncService, "fetch_external_users", AsyncMock(return_value=external)), \
         patch("app.services.user_sync_service.AuthService.generate_api_key", AsyncMock()) as mock_create:
        result = await UserSyncService.run_sync(db, config=config)

    assert result == {"created": 1, "updated": 0, "failed": 0}
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["user_name"] == "alice"
    assert "user_id" not in mock_create.await_args.kwargs


@pytest.mark.asyncio
async def test_run_sync_updates_existing_user_by_username():
    db = AsyncMock()
    existing = SimpleNamespace(id=9, user_name="alice", real_name="Old", remark="old")
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    config = ThirdPartyUserSyncConfig(
        enabled=True,
        connection_config_id=1,
        table_name="hr_users",
        field_map=ThirdPartyUserSyncFieldMap(user_name="login_name"),
    )
    external = [{"user_name": "alice", "real_name": "Alice", "remark": "研发", "extra_data": None}]

    with patch.object(UserSyncService, "fetch_external_users", AsyncMock(return_value=external)), \
         patch("app.services.user_sync_service.AuthService.generate_api_key", AsyncMock()) as mock_create:
        result = await UserSyncService.run_sync(db, config=config)

    assert result == {"created": 0, "updated": 1, "failed": 0}
    mock_create.assert_not_called()
    assert existing.real_name == "Alice"
    assert existing.remark == "第三方同步: 研发"
