"""Create and seed the PostgreSQL demo database used for data-source testing."""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from urllib.parse import quote, unquote, urlsplit, urlunsplit


DEFAULT_SOURCE_URL = "postgresql://postgres:postgres123@localhost:5432/appdb"
DEFAULT_DEMO_DB = "nanzi_demo"
MAINTENANCE_DB = "postgres"
PROTECTED_DATABASES = {"postgres", "template0", "template1"}

DEMO_SCHEMA = "demo"
CUSTOMERS_TABLE = f"{DEMO_SCHEMA}.customers"
PRODUCTS_TABLE = f"{DEMO_SCHEMA}.products"
ORDERS_TABLE = f"{DEMO_SCHEMA}.orders"


def build_database_url(source_url: str, database: str) -> str:
    """Return *source_url* with only its database path replaced."""
    if not database or "/" in database or "\\" in database:
        raise ValueError("database must be a non-empty PostgreSQL database name")

    parsed = urlsplit(source_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ValueError("source URL must use the postgres or postgresql scheme")

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{quote(database, safe='')}",
            parsed.query,
            parsed.fragment,
        )
    )


def build_schema_statements() -> tuple[str, ...]:
    """Return idempotent DDL for the demo schema and its metadata."""
    return (
        f"CREATE SCHEMA IF NOT EXISTS {DEMO_SCHEMA};",
        f"""
        CREATE TABLE IF NOT EXISTS {CUSTOMERS_TABLE} (
            customer_id INTEGER PRIMARY KEY,
            customer_name VARCHAR(120) NOT NULL,
            region VARCHAR(40) NOT NULL,
            industry VARCHAR(80) NOT NULL,
            created_at DATE NOT NULL
        );
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {PRODUCTS_TABLE} (
            product_id INTEGER PRIMARY KEY,
            product_name VARCHAR(120) NOT NULL,
            category VARCHAR(60) NOT NULL,
            unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0)
        );
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {ORDERS_TABLE} (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES {CUSTOMERS_TABLE}(customer_id),
            product_id INTEGER NOT NULL REFERENCES {PRODUCTS_TABLE}(product_id),
            order_date DATE NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
            amount NUMERIC(14, 2) NOT NULL CHECK (amount >= 0),
            CHECK (amount = quantity * unit_price),
            status VARCHAR(20) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        f"CREATE INDEX IF NOT EXISTS idx_demo_orders_customer ON {ORDERS_TABLE}(customer_id);",
        f"CREATE INDEX IF NOT EXISTS idx_demo_orders_product ON {ORDERS_TABLE}(product_id);",
        f"CREATE INDEX IF NOT EXISTS idx_demo_orders_date ON {ORDERS_TABLE}(order_date);",
        f"""
        DO $block$
        BEGIN
            IF obj_description('{CUSTOMERS_TABLE}'::regclass, 'pg_class') IS NULL THEN
                EXECUTE $comment$COMMENT ON TABLE {CUSTOMERS_TABLE} IS 'Demo customer master data for PostgreSQL data-source tests'$comment$;
            END IF;
        END
        $block$;
        """,
        f"""
        DO $block$
        BEGIN
            IF obj_description('{PRODUCTS_TABLE}'::regclass, 'pg_class') IS NULL THEN
                EXECUTE $comment$COMMENT ON TABLE {PRODUCTS_TABLE} IS 'Demo product master data for PostgreSQL data-source tests'$comment$;
            END IF;
        END
        $block$;
        """,
        f"""
        DO $block$
        BEGIN
            IF obj_description('{ORDERS_TABLE}'::regclass, 'pg_class') IS NULL THEN
                EXECUTE $comment$COMMENT ON TABLE {ORDERS_TABLE} IS 'Demo order fact data for PostgreSQL data-source tests'$comment$;
            END IF;
        END
        $block$;
        """,
        f"""
        DO $block$
        DECLARE
            amount_attnum SMALLINT;
        BEGIN
            SELECT attnum INTO amount_attnum
            FROM pg_attribute
            WHERE attrelid = '{ORDERS_TABLE}'::regclass
              AND attname = 'amount'
              AND NOT attisdropped;
            IF amount_attnum IS NOT NULL
               AND col_description('{ORDERS_TABLE}'::regclass, amount_attnum) IS NULL THEN
                EXECUTE $comment$COMMENT ON COLUMN {ORDERS_TABLE}.amount IS '订单金额，等于 quantity * unit_price'$comment$;
            END IF;
        END
        $block$;
        """,
    )


CUSTOMER_ROWS = (
    (1, "华东智造有限公司", "华东", "制造业", date(2025, 1, 15)),
    (2, "北辰零售集团", "华北", "零售", date(2025, 2, 3)),
    (3, "南方物流科技", "华南", "物流", date(2025, 2, 18)),
    (4, "西部能源服务", "西南", "能源", date(2025, 3, 9)),
    (5, "中原医疗器械", "华中", "医疗", date(2025, 3, 22)),
)

PRODUCT_ROWS = (
    (1, "智能传感器", "工业设备", 680.00),
    (2, "数据采集网关", "工业设备", 1280.00),
    (3, "库存分析服务", "软件服务", 3600.00),
    (4, "物流追踪终端", "物流设备", 920.00),
    (5, "经营分析订阅", "软件服务", 2400.00),
)

ORDER_ROWS = (
    (1001, 1, 1, date(2025, 4, 2), 12, 680.00, 8160.00, "completed"),
    (1002, 1, 3, date(2025, 4, 18), 2, 3600.00, 7200.00, "completed"),
    (1003, 2, 5, date(2025, 5, 6), 5, 2400.00, 12000.00, "completed"),
    (1004, 2, 4, date(2025, 5, 21), 8, 920.00, 7360.00, "processing"),
    (1005, 3, 2, date(2025, 6, 3), 6, 1280.00, 7680.00, "completed"),
    (1006, 3, 4, date(2025, 6, 19), 15, 920.00, 13800.00, "completed"),
    (1007, 4, 1, date(2025, 7, 4), 20, 680.00, 13600.00, "completed"),
    (1008, 4, 3, date(2025, 7, 17), 1, 3600.00, 3600.00, "cancelled"),
    (1009, 5, 5, date(2025, 8, 1), 3, 2400.00, 7200.00, "completed"),
    (1010, 5, 2, date(2025, 8, 14), 4, 1280.00, 5120.00, "processing"),
    (1011, 1, 5, date(2025, 9, 5), 2, 2400.00, 4800.00, "completed"),
    (1012, 3, 3, date(2025, 9, 23), 2, 3600.00, 7200.00, "completed"),
)


def _load_psycopg():
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:  # pragma: no cover - exercised by CLI users
        raise RuntimeError(
            "psycopg is required; install it with: pip install 'psycopg[binary]>=3.2,<4'"
        ) from exc
    return psycopg, sql


def build_seed_statements() -> tuple[str, ...]:
    """Return parameterized, idempotent insert statements for demo rows."""
    return (
        f"""
        INSERT INTO {CUSTOMERS_TABLE}
            (customer_id, customer_name, region, industry, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (customer_id) DO NOTHING
        """,
        f"""
        INSERT INTO {PRODUCTS_TABLE}
            (product_id, product_name, category, unit_price)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (product_id) DO NOTHING
        """,
        f"""
        INSERT INTO {ORDERS_TABLE}
            (order_id, customer_id, product_id, order_date, quantity,
             unit_price, amount, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (order_id) DO NOTHING
        """,
    )


def ensure_database(source_url: str, database: str) -> None:
    """Create the target database if it does not exist."""
    psycopg, sql = _load_psycopg()
    maintenance_url = build_database_url(source_url, MAINTENANCE_DB)
    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cursor.fetchone() is None:
                try:
                    cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                except psycopg.errors.DuplicateDatabase:
                    print(f"Database was created concurrently: {database}")
                else:
                    print(f"Created database: {database}")
            else:
                print(f"Database already exists: {database}")


def _seed(connection) -> None:
    with connection.cursor() as cursor:
        for statement in build_schema_statements():
            cursor.execute(statement)

        for statement, rows in zip(
            build_seed_statements(),
            (CUSTOMER_ROWS, PRODUCT_ROWS, ORDER_ROWS),
        ):
            cursor.executemany(statement, rows)


def initialize(source_url: str, database: str) -> tuple[dict[str, int], str]:
    validate_target_database(source_url, database)
    ensure_database(source_url, database)
    psycopg, _ = _load_psycopg()
    target_url = build_database_url(source_url, database)

    with psycopg.connect(target_url) as connection:
        _seed(connection)
        with connection.cursor() as cursor:
            counts = {}
            for table in (CUSTOMERS_TABLE, PRODUCTS_TABLE, ORDERS_TABLE):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = cursor.fetchone()[0]
            cursor.execute(f"SELECT COALESCE(SUM(amount), 0)::text FROM {ORDERS_TABLE}")
            total_amount = cursor.fetchone()[0]
        connection.commit()
    return counts, total_amount


def validate_target_database(source_url: str, database: str) -> None:
    """Reject system databases and accidental writes to the source database."""
    if database in PROTECTED_DATABASES:
        raise ValueError(f"refusing to initialize protected database: {database}")

    source_database = unquote(urlsplit(source_url).path.lstrip("/")).strip()
    if source_database == database:
        raise ValueError(
            "source and target databases must differ; use another database URL as the source"
        )
    if not re.fullmatch(r"nanzi_demo(?:_[a-z0-9]+)*", database):
        raise ValueError("target database must use the nanzi_demo name prefix")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-url",
        default=os.getenv("POSTGRES_SOURCE_URL", DEFAULT_SOURCE_URL),
        help="Any PostgreSQL URL with the target host and credentials",
    )
    parser.add_argument(
        "--database",
        default=os.getenv("POSTGRES_DEMO_DB", DEFAULT_DEMO_DB),
        help=f"Demo database to create or initialize (default: {DEFAULT_DEMO_DB})",
    )
    args = parser.parse_args(argv)

    try:
        counts, total_amount = initialize(args.source_url, args.database)
    except Exception as exc:
        print(f"PostgreSQL demo initialization failed: {exc}", file=sys.stderr)
        if "permission" in str(exc).lower() or "createdb" in str(exc).lower():
            print(
                "The PostgreSQL user may need CREATEDB permission, or create the demo database manually first.",
                file=sys.stderr,
            )
        return 1
    print("PostgreSQL demo database initialized successfully")
    for table, count in counts.items():
        print(f"  {table}: {count} rows")
    print(f"  {ORDERS_TABLE}.total_amount: {total_amount}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
