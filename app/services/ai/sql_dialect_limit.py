"""各 SQL 方言的行数限制片段生成（探查/诊断 SQL 共用）。"""

from __future__ import annotations

import re

import sqlglot
from sqlglot.errors import ParseError


def clamp_row_limit(limit: int, *, max_limit: int = 20, min_limit: int = 1) -> int:
    return max(min_limit, min(int(limit), max_limit))


def apply_dialect_row_limit(
    select_sql: str,
    *,
    dialect: str,
    limit: int,
    max_limit: int = 20,
    min_limit: int = 1,
) -> str:
    """
    为完整 SELECT 语句追加方言正确的行数上限。

    - Oracle：外层 ROWNUM 子查询（兼容 11g，避免 ROWNUM 与内层 WHERE 顺序问题）
    - SQL Server：SELECT TOP N
    - MySQL / ClickHouse / PostgreSQL 等：LIMIT N
    """
    base = str(select_sql or "").strip().rstrip(";")
    if not base:
        return base
    safe_limit = clamp_row_limit(limit, max_limit=max_limit, min_limit=min_limit)
    dialect_lower = str(dialect or "clickhouse").lower()

    if "oracle" in dialect_lower:
        return f"SELECT * FROM ({base}) WHERE ROWNUM <= {safe_limit}"

    if any(token in dialect_lower for token in ("sqlserver", "mssql", "tsql")):
        top_match = re.search(r"(?is)\btop\s*\(?\s*(\d+)\s*\)?", base)
        if top_match:
            current_limit = int(top_match.group(1))
            if current_limit > safe_limit:
                return base[: top_match.start(1)] + str(safe_limit) + base[top_match.end(1) :]
            return base
        try:
            expression = sqlglot.parse_one(base, read="tsql")
            return expression.limit(safe_limit).sql(dialect="tsql")
        except (ParseError, ValueError):
            if re.match(r"(?is)^\s*select\s+distinct\s+", base):
                return re.sub(
                    r"(?is)^\s*select\s+distinct\s+",
                    f"SELECT DISTINCT TOP {safe_limit} ",
                    base,
                    count=1,
                )
            return re.sub(r"(?is)^\s*select\s+", f"SELECT TOP {safe_limit} ", base, count=1)

    upper = base.upper()
    if " LIMIT " in f" {upper} " or upper.rstrip().endswith(" LIMIT"):
        return base
    if " FETCH FIRST " in upper and " ROWS ONLY" in upper:
        return base
    return f"{base} LIMIT {safe_limit}"


def dialect_limit_hint(dialect: str) -> str:
    """人类可读的分页语法提示，用于 repair 文案。"""
    dialect_lower = str(dialect or "clickhouse").lower()
    if "oracle" in dialect_lower:
        return "ROWNUM <= N 或 FETCH FIRST N ROWS ONLY"
    if any(token in dialect_lower for token in ("sqlserver", "mssql", "tsql")):
        return "TOP N"
    return "LIMIT N"
