"""``config_store`` 纯逻辑测试：字段规整与校验、upsert 语句方言（均不触库）。"""

import pytest
from sqlalchemy.dialects import mysql, postgresql

from app.services.ai.observability import config_store

pytestmark = pytest.mark.no_infrastructure


def test_normalize_applies_defaults_for_missing_values():
    row = config_store.normalize_payload({})
    assert row["enabled"] is False
    assert row["sample_rate"] == 1.0
    assert row["capture_content"] is True
    assert row["timeout_seconds"] == 5
    assert row["host"] is None
    assert row["public_key"] is None
    assert row["secret_key"] is None
    assert row["environment"] is None
    assert row["release"] is None
    assert row["trace_url_template"] is None


def test_normalize_coerces_types_and_trims_strings():
    row = config_store.normalize_payload(
        {
            "enabled": "true",
            "sample_rate": "0.25",
            "capture_content": "false",
            "timeout_seconds": "7",
            "host": " http://lf:3000 ",
            "environment": " prod ",
        }
    )
    assert row["enabled"] is True
    assert row["sample_rate"] == 0.25
    assert row["capture_content"] is False
    assert row["timeout_seconds"] == 7
    assert row["host"] == "http://lf:3000"
    assert row["environment"] == "prod"


@pytest.mark.parametrize("value", [True, 1, "1", "true", "TRUE", "yes", "on"])
def test_normalize_accepts_truthy_forms(value):
    assert config_store.normalize_payload({"enabled": value})["enabled"] is True


@pytest.mark.parametrize("value", [False, 0, "0", "false", "FALSE", "no", "off"])
def test_normalize_accepts_falsy_forms(value):
    assert config_store.normalize_payload({"enabled": value})["enabled"] is False


def test_empty_string_becomes_none_for_optional_fields():
    row = config_store.normalize_payload({"host": "  ", "public_key": "", "environment": "  "})
    assert row["host"] is None
    assert row["public_key"] is None
    assert row["environment"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"host": "lf:3000"},
        {"host": "ftp://lf:3000"},
        {"sample_rate": "1.5"},
        {"sample_rate": "-0.1"},
        {"sample_rate": "abc"},
        {"timeout_seconds": "0"},
        {"timeout_seconds": "-3"},
        {"timeout_seconds": "abc"},
        {"enabled": "maybe"},
        {"environment": "x" * 65},
        {"release": "x" * 65},
        {"public_key": "x" * 256},
        {"host": "http://" + "x" * 512},
        {"trace_url_template": "x" * 513},
    ],
)
def test_normalize_rejects_invalid_values(payload):
    with pytest.raises(config_store.LangfuseConfigError):
        config_store.normalize_payload(payload)


def test_normalize_ignores_unknown_keys():
    row = config_store.normalize_payload({"enabled": True, "unknown": "x"})
    assert "unknown" not in row
    assert row["enabled"] is True


def test_normalize_keeps_secret_key_verbatim():
    row = config_store.normalize_payload({"secret_key": "sk-lf-1"})
    assert row["secret_key"] == "sk-lf-1"


def test_normalize_flags_blank_secret_as_delete_marker():
    # 空串表示「清空密钥」，None 表示「未提交该字段」——两者语义不同，必须可区分。
    assert config_store.normalize_payload({"secret_key": ""})["secret_key"] == ""
    assert config_store.normalize_payload({})["secret_key"] is None


def test_public_view_hides_secret_and_reports_presence():
    view = config_store.to_public_view(
        {
            "enabled": True,
            "host": "http://lf:3000",
            "public_key": "pk",
            "secret_key": "sk-lf-1",
            "sample_rate": 1.0,
            "capture_content": True,
            "environment": None,
            "release": None,
            "timeout_seconds": 5,
            "trace_url_template": None,
        }
    )
    assert "secret_key" not in view
    assert view["has_secret_key"] is True
    assert view["enabled"] is True


def test_public_view_reports_missing_secret():
    view = config_store.to_public_view(config_store.normalize_payload({}))
    assert view["has_secret_key"] is False


def test_upsert_statement_supports_both_dialects():
    values = {"id": 1, "enabled": True, "host": "http://lf:3000", "updated_by": "tester"}

    mysql_stmt = config_store._build_upsert(values, "mysql")
    mysql_sql = str(mysql_stmt.compile(dialect=mysql.dialect()))
    assert "ON DUPLICATE KEY UPDATE" in mysql_sql
    assert "langfuse_config" in mysql_sql

    pg_stmt = config_store._build_upsert(values, "postgresql")
    pg_sql = str(pg_stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO UPDATE" in pg_sql
    assert "langfuse_config" in pg_sql


def test_upsert_rejects_unknown_dialect():
    with pytest.raises(config_store.LangfuseConfigError):
        config_store._build_upsert({"id": 1}, "sqlite")
