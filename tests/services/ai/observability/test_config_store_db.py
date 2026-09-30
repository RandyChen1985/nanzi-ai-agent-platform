"""``config_store`` 落库行为测试。

**默认跳过**：这些用例会真实读写 ``langfuse_config`` 表，而项目 conftest 里所有测试
共用同一个开发库。曾经因为"随手跑一次 pytest"把开发库里已保存的真实配置（含加密密钥）
删掉且无法恢复，故这里加开关——只有显式设置环境变量才执行：

    LANGFUSE_DB_TESTS=1 pytest tests/services/ai/observability/test_config_store_db.py

CI / 联调环境应设置该变量（届时表还需已应用 ``db-prod/V161`` 或 ``db-prod-pg/V62``）。
"""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import mysql, postgresql

from app.core.orm import AsyncSessionLocal
from app.services.ai.observability import config_store

DB_TESTS_ENV = "LANGFUSE_DB_TESTS"
_TRUTHY = {"1", "true", "yes", "on"}


def db_tests_enabled() -> bool:
    """是否显式允许落库测试碰真实数据库。"""
    return (os.environ.get(DB_TESTS_ENV) or "").strip().lower() in _TRUTHY


async def _table_exists() -> bool:
    """探测表是否存在。

    这里刻意双层 try：MySQL 在「表不存在」后会让 session 处于失败状态，
    ``async with`` 退出的回滚阶段可能再抛一次，单层 try 兜不住。
    """
    try:
        async with AsyncSessionLocal() as session:
            try:
                await session.execute(text("SELECT 1 FROM langfuse_config LIMIT 1"))
                return True
            except Exception:
                await session.rollback()
                return False
    except Exception:
        return False


@pytest.fixture(autouse=True)
async def _require_table():
    if not db_tests_enabled():
        pytest.skip(
            f"落库测试默认跳过（需显式设置 {DB_TESTS_ENV}=1）"
            "：它会在真实数据库中读写 langfuse_config"
        )
    if not await _table_exists():
        pytest.skip("langfuse_config 表尚未创建（需先应用 V161/V62 迁移）")


@pytest.fixture(autouse=True)
async def _clean(_require_table):
    """测试前备份、测试后还原，**绝不永久删除真实配置**。

    血泪教训：本夹具原先只做 DELETE + commit，会把开发库/联调库里已保存的
    Langfuse 配置（含加密密钥）直接删掉，而该表没有变更审计、删了无法恢复。
    现在改成可逆：先快照原行，用例跑完把原行写回；原本就没有行则保持无行。
    """
    backup = await config_store.snapshot_row_for_tests()
    await config_store.delete_config_for_tests()
    yield
    await config_store.delete_config_for_tests()
    if backup is not None:
        await config_store.restore_row_for_tests(backup)


async def test_load_returns_defaults_when_row_missing():
    config = await config_store.load_config()
    assert config["enabled"] is False
    assert config["sample_rate"] == 1.0
    assert config["secret_key"] is None


async def test_save_then_load_roundtrip_encrypts_secret():
    await config_store.save_config(
        {
            "enabled": True,
            "host": "http://lf:3000",
            "public_key": "pk-lf-1",
            "secret_key": "sk-lf-secret",
            "sample_rate": 0.5,
            "capture_content": False,
            "timeout_seconds": 9,
        },
        changed_by="tester",
    )

    raw = await config_store.load_raw_row()
    assert raw["secret_key"].startswith("langfusekey:v1:")
    assert "sk-lf-secret" not in raw["secret_key"]

    loaded = await config_store.load_config()
    assert loaded["secret_key"] == "sk-lf-secret"
    assert loaded["enabled"] is True
    assert loaded["capture_content"] is False
    assert loaded["sample_rate"] == 0.5
    assert loaded["timeout_seconds"] == 9
    assert raw["updated_by"] == "tester"


async def test_save_without_secret_key_keeps_previous_secret():
    await config_store.save_config({"secret_key": "sk-lf-1"}, changed_by="a")
    await config_store.save_config({"host": "http://lf:3000"}, changed_by="b")
    assert (await config_store.load_config())["secret_key"] == "sk-lf-1"


async def test_save_with_blank_secret_clears_it():
    await config_store.save_config({"secret_key": "sk-lf-1"}, changed_by="a")
    await config_store.save_config({"secret_key": ""}, changed_by="b")
    assert (await config_store.load_config())["secret_key"] is None
    assert (await config_store.load_raw_row())["secret_key"] is None


async def test_save_invalid_payload_does_not_write():
    with pytest.raises(config_store.LangfuseConfigError):
        await config_store.save_config({"sample_rate": 9}, changed_by="a")
    assert (await config_store.load_raw_row()) is None


async def test_save_is_idempotent_single_row():
    await config_store.save_config({"host": "http://a:1"}, changed_by="a")
    await config_store.save_config({"host": "http://b:2"}, changed_by="b")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM langfuse_config"))
        assert int(result.scalar_one()) == 1


async def test_updated_by_is_recorded_per_save():
    await config_store.save_config({"host": "http://a:1"}, changed_by="first")
    await config_store.save_config({"host": "http://b:2"}, changed_by="second")
    assert (await config_store.load_raw_row())["updated_by"] == "second"


async def test_snapshot_restore_roundtrip_preserves_saved_config():
    """锁定「备份 → 删除 → 还原」能完整回到原状（含加密密钥）。

    回归背景：测试夹具曾直接 DELETE 真实配置且无法恢复（该表没有变更审计）。
    这条用例保证还原能力本身是可用的——它是夹具可逆性的地基。
    """
    await config_store.save_config(
        {
            "enabled": True,
            "host": "http://keep-me:3000",
            "public_key": "pk-keep",
            "secret_key": "sk-keep",
            "sample_rate": 0.25,
            "capture_content": False,
            "environment": "staging",
            "release": "v9",
            "timeout_seconds": 12,
            "trace_url_template": "http://keep-me:3000/trace/{trace_id}",
        },
        changed_by="real_user",
    )

    backup = await config_store.snapshot_row_for_tests()
    await config_store.delete_config_for_tests()
    assert await config_store.load_raw_row() is None, "删除应生效"

    await config_store.restore_row_for_tests(backup)

    restored = await config_store.load_config()
    assert restored["enabled"] is True
    assert restored["host"] == "http://keep-me:3000"
    assert restored["public_key"] == "pk-keep"
    assert restored["secret_key"] == "sk-keep", "加密密钥必须能原样还原"
    assert restored["sample_rate"] == 0.25
    assert restored["capture_content"] is False
    assert restored["environment"] == "staging"
    assert restored["release"] == "v9"
    assert restored["timeout_seconds"] == 12
    assert restored["trace_url_template"] == "http://keep-me:3000/trace/{trace_id}"
    assert restored["updated_by"] == "real_user"
