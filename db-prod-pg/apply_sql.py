"""Apply a PostgreSQL baseline or migration file to an explicit database.

This importer deliberately keeps database creation and schema application as
two separate connections.  The target database is never inferred from the
SQL file and is never interpolated into SQL as raw user input.
"""

import argparse
import getpass
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql


DATABASE_SWITCH_RE = re.compile(
    r"^\s*(?:CREATE\s+DATABASE\b|DROP\s+DATABASE\b|ALTER\s+DATABASE\b|\\connect\b|\\c\b)",
    re.IGNORECASE,
)
DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROTECTED_DATABASES = frozenset({"postgres", "template0", "template1"})


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def validate_target_database(database: str) -> str:
    """Validate a database name before it is used with ``sql.Identifier``."""

    if not database or not DATABASE_NAME_RE.fullmatch(database):
        raise ValueError(
            "target database must be a non-empty PostgreSQL identifier "
            "containing only letters, digits, and underscores"
        )
    if database.lower() in PROTECTED_DATABASES:
        raise ValueError(f"refusing to modify protected PostgreSQL database: {database}")
    return database


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply a SQL migration to an explicitly selected PostgreSQL database."
    )
    parser.add_argument("file_path", nargs="?", help="SQL file to execute")
    parser.add_argument("--host", help="PostgreSQL host")
    parser.add_argument("--port", type=int, default=5432, help="PostgreSQL port")
    parser.add_argument("--user", help="PostgreSQL user")
    parser.add_argument(
        "--password",
        help="PostgreSQL password; omit with --interactive to prompt securely",
    )
    parser.add_argument("--database", "--db", dest="database", help="Target database name")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for missing connection fields before confirming execution",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the per-file confirmation. Use only after an outer wrapper confirmed.",
    )
    parser.add_argument(
        "--list-tables",
        action="store_true",
        help="List existing base tables in public schema as JSON and exit",
    )
    args = parser.parse_args(argv)

    if not args.list_tables and not args.file_path:
        parser.error("the following arguments are required: file_path")

    missing = [name for name in ("host", "user", "database") if not getattr(args, name)]
    if missing and not args.interactive:
        parser.error(
            "missing explicit connection parameter(s): "
            + ", ".join(f"--{name}" for name in missing)
            + ". Use --interactive to enter them safely."
        )

    return args


def prompt_if_missing(label, current, secret=False):
    if current is not None:
        return current
    if secret:
        return getpass.getpass(f"{label}: ")
    return input(f"{label}: ").strip()


def build_config(args) -> DbConfig:
    database = prompt_if_missing("Target database", args.database)
    return DbConfig(
        host=prompt_if_missing("PostgreSQL host", args.host),
        port=args.port,
        user=prompt_if_missing("PostgreSQL user", args.user),
        password=prompt_if_missing("PostgreSQL password", args.password, secret=True),
        database=validate_target_database(database),
    )


def _remove_comments(statement: str) -> str:
    """Remove comments for empty-statement detection without touching strings."""

    result = []
    i = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    while i < len(statement):
        char = statement[i]
        next_char = statement[i + 1] if i + 1 < len(statement) else ""

        if in_line_comment:
            if char in "\r\n":
                in_line_comment = False
                result.append(char)
            i += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if not in_double and not in_single and char == "-" and next_char == "-":
            in_line_comment = True
            i += 2
            continue
        if not in_double and not in_single and char == "/" and next_char == "*":
            in_block_comment = True
            i += 2
            continue
        result.append(char)
        if char == "'" and not in_double:
            if in_single and next_char == "'":
                result.append(next_char)
                i += 2
                continue
            in_single = not in_single
        elif char == '"' and not in_single:
            if in_double and next_char == '"':
                result.append(next_char)
                i += 2
                continue
            in_double = not in_double
        i += 1
    return "".join(result)


def _append_statement(statements, current):
    statement = "".join(current).strip()
    if not statement or not _remove_comments(statement).strip():
        return
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if DATABASE_SWITCH_RE.match(statement):
        print(f"⚠️  Skipping database-switching statement: {statement.splitlines()[0]}")
        return
    statements.append(statement)


def split_sql_statements(sql_content: str) -> list[str]:
    """Split PostgreSQL SQL while respecting strings, comments and dollar quotes."""

    statements = []
    current = []
    i = 0
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    dollar_tag = None

    while i < len(sql_content):
        char = sql_content[i]
        next_char = sql_content[i + 1] if i + 1 < len(sql_content) else ""

        if in_line_comment:
            current.append(char)
            if char in "\r\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            current.append(char)
            if char == "*" and next_char == "/":
                current.append(next_char)
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue

        if dollar_tag is not None:
            if sql_content.startswith(dollar_tag, i):
                current.extend(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(char)
                i += 1
            continue

        if not in_single and not in_double and char == "-" and next_char == "-":
            current.extend((char, next_char))
            in_line_comment = True
            i += 2
            continue
        if not in_single and not in_double and char == "/" and next_char == "*":
            current.extend((char, next_char))
            in_block_comment = True
            i += 2
            continue

        if not in_double and char == "'":
            current.append(char)
            if in_single and next_char == "'":
                current.append(next_char)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue

        if not in_single and char == '"':
            current.append(char)
            if in_double and next_char == '"':
                current.append(next_char)
                i += 2
                continue
            in_double = not in_double
            i += 1
            continue

        if not in_single and not in_double and char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", sql_content[i:])
            if match:
                dollar_tag = match.group(0)
                current.extend(dollar_tag)
                i += len(dollar_tag)
                continue

        if char == ";" and not in_single and not in_double:
            current.append(char)
            _append_statement(statements, current)
            current = []
        else:
            current.append(char)
        i += 1

    _append_statement(statements, current)
    return statements


def _connection_kwargs(config: DbConfig, database: str) -> dict:
    return {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "dbname": database,
    }


def ensure_database(config: DbConfig) -> None:
    """Create the target database if needed, using a safe quoted identifier."""

    with psycopg.connect(
        **_connection_kwargs(config, "postgres"),
        autocommit=True,
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (config.database,),
        ).fetchone()
        if exists:
            return
        connection.execute(
            sql.SQL("CREATE DATABASE {} WITH ENCODING 'UTF8'").format(
                sql.Identifier(config.database)
            )
        )


def confirm_execution(config: DbConfig, file_path: str) -> None:
    print("请确认本次 SQL 执行目标：")
    print(f"  Host     : {config.host}")
    print(f"  Port     : {config.port}")
    print(f"  User     : {config.user}")
    print(f"  Database : {config.database}")
    print(f"  SQL file : {file_path}")
    print("  Password : ******")
    answer = input("确认无误请输入 YES 继续执行：").strip()
    if answer.upper() != "YES":
        print("❌ 已取消，未执行 SQL。")
        raise SystemExit(1)


def apply_sql(file_path: str, config: DbConfig) -> None:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    ensure_database(config)
    sql_content = path.read_text(encoding="utf-8")
    statements = split_sql_statements(sql_content)
    print(f"🚀 Applying {path} to PostgreSQL database '{config.database}' ({len(statements)} statements)...")

    with psycopg.connect(**_connection_kwargs(config, config.database)) as connection:
        with connection.transaction():
            for index, statement in enumerate(statements, start=1):
                try:
                    cursor = connection.execute(statement)
                    print(f"   -> #{index}: affected rows {cursor.rowcount}")
                except psycopg.Error as error:
                    print(f"❌ 执行失败 #{index}:\n{statement[:200]}...\nError: {error}")
                    raise
    print("✅ SQL applied successfully.")


def list_database_tables(config: DbConfig) -> int:
    """Print the list of base tables in the target database as a JSON array."""
    import json
    try:
        with psycopg.connect(**_connection_kwargs(config, config.database)) as connection:
            rows = connection.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename;
                """
            ).fetchall()
            print(json.dumps([r[0] for r in rows]))
        return 0
    except Exception:
        print(json.dumps([]))
        return 0


def main(argv=None):
    args = parse_args(argv)
    try:
        config = build_config(args)
        if args.list_tables:
            return list_database_tables(config)
        if not args.yes:
            confirm_execution(config, args.file_path)
        apply_sql(args.file_path, config)
    except (FileNotFoundError, ValueError, psycopg.Error) as error:
        print(f"❌ {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
