# PostgreSQL Demo Database Implementation Plan

> **For agentic workers:** This plan is executed inline in the current session. Git staging and commits are intentionally omitted because the user requested direct execution without Git finalization.

**Goal:** Add an idempotent PostgreSQL demo database initializer and use it to create `nanzi_demo` with representative ChatBI test data.

**Architecture:** Keep URL parsing and SQL definitions in `scripts/init_postgres_demo.py`; connect first to the maintenance database to create the target database, then connect to the target database to create the `demo` schema and seed rows. The script never drops the target database or tables.

**Tech Stack:** Python 3.11+, `psycopg` 3 with binary wheels, PostgreSQL, pytest.

---

### Task 1: Add failing unit tests for URL and schema contracts

**Files:**
- Create: `tests/test_postgres_demo_script.py`

- [x] **Step 1: Write the failing test**

Cover the required pure behavior without opening a database:

```python
from scripts.init_postgres_demo import (
    DEMO_SCHEMA,
    ORDERS_TABLE,
    build_database_url,
    build_schema_statements,
)


def test_build_database_url_replaces_only_database_name():
    source = "postgresql://postgres:postgres123@localhost:5432/appdb?sslmode=disable"

    assert build_database_url(source, "nanzi_demo") == (
        "postgresql://postgres:postgres123@localhost:5432/nanzi_demo?sslmode=disable"
    )


def test_schema_statements_define_demo_orders_and_idempotent_seed_contract():
    sql = "\n".join(build_schema_statements())

    assert DEMO_SCHEMA in sql
    assert ORDERS_TABLE in sql
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "ON CONFLICT" in sql
```

- [x] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_postgres_demo_script.py -q
```

Expected: collection fails because `scripts.init_postgres_demo` does not exist yet.

### Task 2: Implement the initializer and dependency

**Files:**
- Create: `scripts/init_postgres_demo.py`
- Modify: `requirements.txt`

- [x] **Step 1: Add `psycopg[binary]>=3.2,<4` to `requirements.txt`**

- [x] **Step 2: Implement URL replacement**

Expose `build_database_url(source_url, database)` using `urllib.parse`, preserving credentials, host, port, query parameters and fragments while replacing only the database path.

- [x] **Step 3: Implement schema and seed definitions**

Create `demo.customers`, `demo.products`, and `demo.orders` with primary keys, foreign keys, positive quantity checks, numeric amounts, indexes and guarded PostgreSQL comments. Use fixed rows of 5 customers, 5 products and 12 orders. Insert with fixed primary keys and `ON CONFLICT ... DO NOTHING`.

- [x] **Step 4: Implement database creation and initialization**

Connect to the maintenance database `postgres` with autocommit, check `pg_database`, and create the requested database through `psycopg.sql.Identifier`. Connect to the target database, execute schema statements and seed rows in a transaction, then print counts and total order amount.

- [x] **Step 5: Add CLI arguments**

Support `--source-url` and `--database`, with defaults from `POSTGRES_SOURCE_URL` and `POSTGRES_DEMO_DB`, falling back to the user-provided local connection and `nanzi_demo`.

### Task 3: Verify locally and against PostgreSQL

**Files:**
- Test: `tests/test_postgres_demo_script.py`

- [x] **Step 1: Install the new dependency into the existing virtual environment**

Run:

```bash
.venv/bin/python -m pip install 'psycopg[binary]>=3.2,<4'
```

- [x] **Step 2: Run the focused unit tests**

```bash
.venv/bin/python -m pytest tests/test_postgres_demo_script.py -q
```

Expected: all tests pass.

- [x] **Step 3: Run the initializer with the supplied source URL**

```bash
.venv/bin/python scripts/init_postgres_demo.py --source-url 'postgresql://postgres:postgres123@localhost:5432/appdb'
```

Expected: create or reuse `nanzi_demo`, report 5 customers, 5 products and 12 orders, and print a non-zero order total.

- [x] **Step 4: Run it a second time**

Expected: it succeeds with the same counts, proving idempotence.

- [x] **Step 5: Run final checks**

```bash
.venv/bin/python -m py_compile scripts/init_postgres_demo.py
git diff --check
```

Do not run `./dev.sh`, do not restart services, and do not stage or commit files.
