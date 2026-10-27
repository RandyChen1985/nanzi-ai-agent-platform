from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import column
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError

from app.api.portal.endpoints import dashboard
from app.services.config_service import ConfigService, build_system_config_upsert_statement
from app.services.knowledge_metrics_service import build_knowledge_metrics_upsert_statement
from app.services.memory_config_service import MemoryConfigService
from app.services.partition_service import PartitionService
from app.services.ai.scheduler_service import TaskSchedulerService
from app.services.db_profile_service import DbProfileService
from app.models.db_connection import DbTableProfile
from app.core.database import init_db


pytestmark = pytest.mark.no_infrastructure


def _sql(statement, dialect):
    return str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def test_system_config_upsert_compiles_for_mysql_and_postgresql():
    mysql_sql = _sql(
        build_system_config_upsert_statement(
            key="llm_model_name",
            value="DeepSeek-V3.2",
            description=None,
            category="llm",
            is_secret=False,
            dialect_name="mysql",
        ),
        mysql.dialect(),
    )
    postgresql_sql = _sql(
        build_system_config_upsert_statement(
            key="llm_model_name",
            value="DeepSeek-V3.2",
            description=None,
            category="llm",
            is_secret=False,
            dialect_name="postgresql",
        ),
        postgresql.dialect(),
    )

    assert "ON DUPLICATE KEY UPDATE" in mysql_sql
    assert "`key`" in mysql_sql
    assert 'ON CONFLICT ("key") DO UPDATE' in postgresql_sql
    assert '"key"' in postgresql_sql


def test_knowledge_metrics_upsert_accumulates_counts_for_both_dialects():
    kwargs = {
        "metric_date": date(2026, 7, 27),
        "target_type": "document",
        "target_id": "doc-1",
        "target_name": "文档一",
        "search_count": 3,
        "citation_count": 2,
    }
    mysql_sql = _sql(
        build_knowledge_metrics_upsert_statement(**kwargs, dialect_name="mysql"),
        mysql.dialect(),
    )
    postgresql_sql = _sql(
        build_knowledge_metrics_upsert_statement(**kwargs, dialect_name="postgresql"),
        postgresql.dialect(),
    )

    assert "ON DUPLICATE KEY UPDATE" in mysql_sql
    assert "knowledge_base_metrics.search_count + VALUES(search_count)" in mysql_sql
    assert "ON CONFLICT ON CONSTRAINT uix_kb_metric_date_target DO UPDATE" in postgresql_sql
    assert 'knowledge_base_metrics.search_count + excluded.search_count' in postgresql_sql


def test_db_profile_field_count_uses_native_json_array_function():
    mysql_sql = _sql(
        DbProfileService._profile_field_count_expression("mysql"),
        mysql.dialect(),
    )
    postgresql_sql = _sql(
        DbProfileService._profile_field_count_expression("postgresql"),
        postgresql.dialect(),
    )

    assert "json_length(db_table_profiles.columns_profile)" in mysql_sql.lower()
    assert "jsonb_array_length(db_table_profiles.columns_profile)" in postgresql_sql.lower()


def test_db_profile_tag_filter_uses_native_json_contains_operator():
    from sqlalchemy import select

    mysql_sql = _sql(
        DbProfileService._apply_profile_filters(
            select(DbTableProfile), tag="交易", dialect_name="mysql"
        ),
        mysql.dialect(),
    )
    postgresql_sql = str(
        DbProfileService._apply_profile_filters(
            select(DbTableProfile), tag="交易", dialect_name="postgresql"
        ).compile(dialect=postgresql.dialect())
    )

    assert "json_contains" in mysql_sql.lower()
    assert " @> " in postgresql_sql
    assert "json_contains" not in postgresql_sql.lower()


def test_dashboard_hour_key_expression_uses_native_date_functions():
    created_at = column("created_at")

    assert hasattr(dashboard, "_hour_key_expression")
    mysql_sql = _sql(
        dashboard._hour_key_expression(created_at, "mysql"),
        mysql.dialect(),
    ).lower()
    postgresql_sql = _sql(
        dashboard._hour_key_expression(created_at, "postgresql"),
        postgresql.dialect(),
    ).lower()

    assert "date_format" in mysql_sql
    assert "date_format" not in postgresql_sql
    assert "date_trunc('hour'" in postgresql_sql
    assert "to_char" in postgresql_sql


@pytest.mark.asyncio
async def test_memory_config_queries_do_not_use_mysql_backtick_identifiers():
    session = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    session.execute = AsyncMock(return_value=result)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.memory_config_service.AsyncSessionLocal",
        return_value=session_cm,
    ):
        await MemoryConfigService.get_all()

    statement = session.execute.await_args.args[0]
    assert "`" not in str(statement)


@pytest.mark.asyncio
async def test_config_get_does_not_write_to_disabled_redis_after_db_hit():
    session = MagicMock()
    result = MagicMock()
    result.fetchone.return_value = ("v1",)
    session.execute = AsyncMock(return_value=result)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.config_service.get_redis",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.services.config_service.AsyncSessionLocal",
        return_value=session_cm,
    ), patch("app.services.config_service.logger.error") as log_error:
        assert await ConfigService.get("probe") == "v1"

    log_error.assert_not_called()


@pytest.mark.asyncio
async def test_partition_maintenance_uses_portable_postgresql_delete_path():
    session = MagicMock()
    delete_result = MagicMock(rowcount=0)
    session.execute = AsyncMock(return_value=delete_result)
    session.commit = AsyncMock()

    with patch(
        "app.services.partition_service.settings",
        SimpleNamespace(normalized_database_type="postgresql"),
    ):
        assert await PartitionService.expand_partitions(session) == {}
        result = await PartitionService.prune_expired_logs(session, retention_days=30)

    assert result["status"] == "success"
    statements = [call.args[0].text for call in session.execute.await_args_list]
    assert statements
    assert all("ctid" in statement for statement in statements)
    assert all("DELETE FROM `" not in statement for statement in statements)


def test_app_registers_sqlalchemy_database_error_handler_for_postgresql():
    from app.main import app

    assert SQLAlchemyOperationalError in app.exception_handlers


@pytest.mark.asyncio
async def test_database_health_check_logs_configured_database_type():
    connection = MagicMock()
    connection.execute = AsyncMock()
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.core.database.settings",
        SimpleNamespace(DATABASE_TYPE="postgresql", normalized_database_type="postgresql"),
    ), patch(
        "app.core.database.engine",
        SimpleNamespace(connect=MagicMock(return_value=connection_context)),
    ), patch("app.core.database.logger.info") as log_info:
        await init_db()

    assert any(
        "DATABASE_TYPE=%s" in call.args[0] and call.args[1:] == ("postgresql", "postgresql")
        for call in log_info.call_args_list
    )


@pytest.mark.asyncio
async def test_scheduler_job_store_uses_selected_sync_database_url():
    service = TaskSchedulerService()
    service._scheduler = None
    fake_scheduler = MagicMock(running=True)
    fake_job_store = MagicMock()
    postgresql_url = "postgresql+psycopg://postgres:secret@localhost:5432/nanzi_demo"

    with patch(
        "app.services.ai.scheduler_service.settings",
        SimpleNamespace(DATABASE_SYNC_URL=postgresql_url, MYSQL_POOL_RECYCLE=1800),
    ), patch(
        "app.services.ai.scheduler_service.SQLAlchemyJobStore",
        return_value=fake_job_store,
    ) as create_job_store, patch(
        "app.services.ai.scheduler_service.AsyncIOScheduler",
        return_value=fake_scheduler,
    ), patch.object(service, "reschedule_third_party_user_sync", new=AsyncMock()), patch.object(
        service, "reload_tasks", new=AsyncMock()
    ), patch.object(service, "reload_saved_report_subscriptions", new=AsyncMock()):
        await service.start()

    # jobstore 是长持有连接的同步 engine：必须显式带上 pool_pre_ping/recycle，
    # 否则 MySQL 侧连接失效后调度会静默停摆（recycle 取自配置，可运维调整）。
    create_job_store.assert_called_once_with(
        url=postgresql_url,
        tablename="ai_agent_scheduler_jobs",
        engine_options={"pool_pre_ping": True, "pool_recycle": 1800},
    )


@pytest.mark.asyncio
async def test_mysql_only_agent_reinit_script_refuses_postgresql():
    from scripts import reinit_system_agents

    with patch(
        "scripts.reinit_system_agents.settings",
        SimpleNamespace(normalized_database_type="postgresql"),
    ), pytest.raises(RuntimeError, match="MySQL-only"):
        await reinit_system_agents.reinit_agents()
