import argparse
import asyncio
import getpass
import os
import re
import sys
from dataclasses import dataclass

import aiomysql
from pymysql.constants import CLIENT


DATABASE_SWITCH_RE = re.compile(r"^\s*(CREATE\s+DATABASE\b|USE\b)", re.IGNORECASE)


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply a SQL migration to an explicitly selected MySQL database."
    )
    parser.add_argument("file_path", nargs="?", help="SQL file to execute")
    parser.add_argument("--host", help="MySQL host")
    parser.add_argument("--port", type=int, default=3306, help="MySQL port")
    parser.add_argument("--user", help="MySQL user")
    parser.add_argument("--password", help="MySQL password; omit with --interactive to prompt securely")
    parser.add_argument("--database", "--db", dest="database", help="Target database name")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for any missing connection fields before confirming execution",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the per-file confirmation. Use only after an outer wrapper has already confirmed.",
    )
    parser.add_argument(
        "--check-charset",
        action="store_true",
        help="Check the target database default charset and exit without executing SQL",
    )
    args = parser.parse_args(argv)

    if not args.check_charset and not args.file_path:
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


def build_config(args):
    return DbConfig(
        host=prompt_if_missing("MySQL host", args.host),
        port=args.port,
        user=prompt_if_missing("MySQL user", args.user),
        password=prompt_if_missing("MySQL password", args.password, secret=True),
        database=prompt_if_missing("Target database", args.database),
    )


def split_sql_statements(sql_content):
    statements = []
    current_statement = []
    in_string = False
    escape = False
    quote_char = None
    
    # 逐字扫描，维护字符串状态，避开字符串内的分号
    for char in sql_content:
        current_statement.append(char)
        
        if escape:
            escape = False
            continue
            
        if char == '\\':
            escape = True
            continue
            
        if char in ("'", '"', '`'):
            if not in_string:
                in_string = True
                quote_char = char
            elif char == quote_char:
                # 闭合字符串
                in_string = False
                quote_char = None
                
        elif char == ';' and not in_string:
            # 遇到不在字符串内的分号，拆分出一条 SQL
            stmt = "".join(current_statement).strip()
            # 过滤注释行
            lines = []
            for line in stmt.splitlines():
                clean_line = line.strip()
                if not clean_line.startswith("--") and not clean_line.startswith("#"):
                    lines.append(line)
            clean_stmt = "\n".join(lines).strip()
            
            # 去除末尾分号
            if clean_stmt.endswith(';'):
                clean_stmt = clean_stmt[:-1].strip()
                
            if clean_stmt:
                if DATABASE_SWITCH_RE.match(clean_stmt):
                    print(f"⚠️  Skipping database-switching statement: {clean_stmt.splitlines()[0]}")
                else:
                    statements.append(clean_stmt)
            current_statement = []
            
    # 处理最后一个没有分号结尾的语句
    if current_statement:
        stmt = "".join(current_statement).strip()
        lines = []
        for line in stmt.splitlines():
            clean_line = line.strip()
            if not clean_line.startswith("--") and not clean_line.startswith("#"):
                lines.append(line)
        clean_stmt = "\n".join(lines).strip()
        if clean_stmt.endswith(';'):
            clean_stmt = clean_stmt[:-1].strip()
        if clean_stmt:
            if not DATABASE_SWITCH_RE.match(clean_stmt):
                statements.append(clean_stmt)
                
    # 条件 DDL 需要把 SET 用户变量、PREPARE、EXECUTE、DEALLOCATE 保持在同一
    # 个 MySQL 会话中；否则每条语句单独执行时，后续 PREPARE 看不到变量。
    merged_statements = []
    prepared_block = []
    for statement_index, statement in enumerate(statements):
        normalized = statement.lstrip().upper()
        if prepared_block:
            prepared_block.append(statement)
            if normalized.startswith("DEALLOCATE PREPARE"):
                merged_statements.append(";\n".join(prepared_block))
                prepared_block = []
            continue

        if normalized.startswith("SET @"):
            # 仅在后续确实紧跟 PREPARE 时进入块，普通 SET @ 仍保持原行为。
            if statement_index + 1 < len(statements) and statements[
                statement_index + 1
            ].lstrip().upper().startswith("PREPARE "):
                prepared_block = [statement]
                continue

        merged_statements.append(statement)

    if prepared_block:
        # 让缺少 DEALLOCATE 的 SQL 继续按原语句执行并由 MySQL 返回明确错误。
        merged_statements.extend(prepared_block)

    return merged_statements


def confirm_execution(config, file_path):
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


async def inspect_database_charset(config):
    """Return the target database default charset/collation, or None if it is absent."""
    conn = await aiomysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
                "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                (config.database,),
            )
            return await cur.fetchone()
    finally:
        conn.close()
        await conn.ensure_closed()


async def check_database_charset(config):
    """Print a preflight result and return 0, 1, or 2 for missing/error/mismatch."""
    try:
        charset_info = await inspect_database_charset(config)
    except Exception as exc:
        print(f"❌ 目标数据库字符集检查失败，未执行 SQL：{exc}")
        return 1

    if charset_info is None:
        print("ℹ️ 目标数据库不存在；确认后将以 utf8mb4 创建。")
        return 0

    charset_name, collation_name = charset_info
    print(f"ℹ️ 目标数据库默认字符集: {charset_name}，排序规则: {collation_name}")
    if str(charset_name).lower() != "utf8mb4":
        print("⚠️ 目标数据库默认字符集不是 utf8mb4，导入包含中文的 SQL 时可能出现乱码。")
        print("⚠️ 如仍要继续，请在上一步确认提示中明确输入 YES。")
        return 2

    return 0


async def apply_sql(file_path, config):
    print(f"🔌 Connecting to MySQL server to ensure database '{config.database}' exists...")

    # 1. 尝试无库连线并自动建库（如果不存在）
    try:
        temp_conn = await aiomysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            autocommit=True,
        )
        async with temp_conn.cursor() as cur:
            create_db_sql = f"CREATE DATABASE IF NOT EXISTS `{config.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
            await cur.execute(create_db_sql)
        temp_conn.close()
        await temp_conn.ensure_closed()
    except Exception as e:
        print(f"❌ Connection or database creation failed: {e}")
        sys.exit(1)

    print(f"🔌 Connecting to database '{config.database}'...")
    try:
        pool = await aiomysql.create_pool(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            db=config.database,
            autocommit=True,
            client_flag=CLIENT.MULTI_STATEMENTS,
        )
    except Exception as e:
        print(f"❌ Connection to '{config.database}' failed: {e}")
        sys.exit(1)

    try:
        if not os.path.exists(file_path):
            print(f"❌ File not found: {file_path}")
            sys.exit(1)

        print(f"📖 Reading {file_path}...")
        with open(file_path, "r", encoding="utf-8") as f:
            sql_content = f.read()

        statements = split_sql_statements(sql_content)

        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                print(f"🚀 Executing {len(statements)} statements...")
                for i, stmt in enumerate(statements):
                    try:
                        await cur.execute(stmt)
                        affected_rows = cur.rowcount
                        # MULTI_STATEMENTS 会为 PREPARE/EXECUTE/DEALLOCATE 产生多个
                        # protocol result；必须消费完，否则下一条 SQL 会遇到未读结果。
                        while await cur.nextset():
                            pass
                        print(f"   -> Affected rows: {affected_rows}")
                    except Exception as sqle:
                        err_code = getattr(sqle, "args", [0])[0]
                        if err_code in (1007, 1050, 1054, 1060, 1061, 1062, 1091):
                            print(f"   -> 幂等跳过（可忽略，对象已存在/已变更）: {sqle}")
                        else:
                            print(f"❌ 执行失败（非幂等可忽略错误，需处理） #{i + 1}:\n{stmt[:100]}...\nError: {sqle}")
                            sys.exit(1)

            await conn.commit()
            print("✅ Transaction committed.")

        print("✅ SQL applied successfully.")

    finally:
        pool.close()
        await pool.wait_closed()


def main(argv=None):
    args = parse_args(argv)
    config = build_config(args)
    if args.check_charset:
        raise SystemExit(asyncio.run(check_database_charset(config)))
    if not args.yes:
        confirm_execution(config, args.file_path)
    asyncio.run(apply_sql(args.file_path, config))


if __name__ == "__main__":
    main()
