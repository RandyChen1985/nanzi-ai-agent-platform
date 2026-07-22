import pytest

from scripts.init_postgres_demo import (
    DEMO_SCHEMA,
    ORDERS_TABLE,
    build_database_url,
    build_seed_statements,
    build_schema_statements,
    validate_target_database,
    CUSTOMER_ROWS,
    ORDER_ROWS,
    PRODUCT_ROWS,
)

pytestmark = pytest.mark.no_infrastructure


def test_build_database_url_replaces_only_database_name():
    source = "postgresql://postgres:postgres123@localhost:5432/appdb?sslmode=disable"

    assert build_database_url(source, "nanzi_demo") == (
        "postgresql://postgres:postgres123@localhost:5432/nanzi_demo?sslmode=disable"
    )


def test_schema_statements_define_demo_orders():
    sql = "\n".join(build_schema_statements())

    assert DEMO_SCHEMA in sql
    assert ORDERS_TABLE in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql


def test_seed_statements_are_idempotent():
    sql = "\n".join(build_seed_statements())

    assert sql.count("ON CONFLICT") == 3
    assert sql.count("DO NOTHING") == 3


def test_demo_seed_rows_have_expected_counts_and_total():
    assert len(CUSTOMER_ROWS) == 5
    assert len(PRODUCT_ROWS) == 5
    assert len(ORDER_ROWS) == 12
    assert sum(row[6] for row in ORDER_ROWS) == 97720.0


def test_target_database_rejects_protected_or_source_database():
    source = "postgresql://postgres:postgres123@localhost:5432/appdb"

    with pytest.raises(ValueError, match="protected"):
        validate_target_database(source, "postgres")
    with pytest.raises(ValueError, match="must differ"):
        validate_target_database(source, "appdb")
    with pytest.raises(ValueError, match="nanzi_demo name prefix"):
        validate_target_database(source, "business_db")
