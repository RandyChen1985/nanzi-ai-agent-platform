"""各 SQL 方言的行数限制片段生成（探查/诊断 SQL 共用）。"""

from __future__ import annotations

import re
from typing import Optional

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


def clamp_row_limit(limit: int, *, max_limit: int = 20, min_limit: int = 1) -> int:
    return max(min_limit, min(int(limit), max_limit))


# 结尾行注释：`-- xxx`（SQL 标准）或 `# xxx`（MySQL，方言里同样是行注释）
_TRAILING_LINE_COMMENT_RE = re.compile(r"^\s*(?:--|#)")


def strip_trailing_comment_and_terminator(select_sql: str) -> str:
    """去掉 SQL 结尾的行注释与分号，只留下语句本身。

    追加 `LIMIT n` 或 Oracle 的 `)` 时，若 SQL 仍以 `-- 注释` 结尾，拼接内容会被
    整行注释吞掉：MySQL 变成没有上限（AI 分析直接拉全表），Oracle 包装则因为少了
    右括号变成语法错误。这里用 sqlglot 的 tokenizer 定位最后一个真实 token 的结束
    位置，而不是正则找 `--`，避免误伤字符串里的 `--`（如 `WHERE name = 'a--b'`）。

    tokenizer 不可用时（语法本身已损坏）退化为止损版本：只去分号，不猜注释位置。
    """
    text = str(select_sql or "").strip()
    if not text:
        return text
    try:
        from sqlglot.tokens import Tokenizer

        tokens = Tokenizer().tokenize(text)
    except Exception:  # noqa: BLE001 - tokenizer 失败不应让调用方报错
        tokens = []
    if tokens:
        # token.end 是最后一个字符的下标（含），其后若只剩行注释就切掉
        last_end = tokens[-1].end + 1
        if _TRAILING_LINE_COMMENT_RE.match(text[last_end:]):
            text = text[:last_end].rstrip()
    # `SELECT ...; -- 注释` 这类：注释剥掉后分号才露出来
    while text.endswith(";"):
        text = text[:-1].rstrip()
    return text


def _read_dialect(dialect_lower: str) -> Optional[str]:
    """平台/数据源方言名 -> sqlglot read 方言；不认识的返回 None（走通用解析）。"""
    if any(token in dialect_lower for token in ("sqlserver", "mssql", "tsql")):
        return "tsql"
    if "postgres" in dialect_lower or dialect_lower == "pg":
        return "postgres"
    for name in ("oracle", "clickhouse", "mysql", "starrocks", "doris", "hive", "spark", "sqlite"):
        if name in dialect_lower:
            return name
    return None


def _parse_outer(sql: str, read: Optional[str]) -> Optional[exp.Expression]:
    try:
        expression = sqlglot.parse_one(sql, read=read) if read else sqlglot.parse_one(sql)
    except (ParseError, ValueError):
        return None
    # Block 是多语句容器（末尾残留分隔符/注释时会出现），无法定位单条 SELECT
    block_type = getattr(exp, "Block", None)
    if isinstance(block_type, type) and isinstance(expression, block_type):
        return None
    return expression


def _outer_limit_node(expression: Optional[exp.Expression]) -> Optional[exp.Expression]:
    """只取最外层 Select/SetOperation 上的 LIMIT / FETCH / TOP。

    旧实现用 `" LIMIT " in sql.upper()` 判断，子查询里的 `LIMIT 10`（以及字符串
    字面量里的 " LIMIT "）会让外层误以为已有上限，结果外层完全不加保护、直接拉
    全表。这里只在 AST 的顶层节点上看 limit（sqlglot 把 T-SQL 的 TOP 和 Oracle 的
    FETCH FIRST 也归到 args["limit"]）。
    """
    if expression is None:
        return None
    set_operation = getattr(exp, "SetOperation", None)
    outer_types = (exp.Select,) + ((set_operation,) if isinstance(set_operation, type) else ())
    if not isinstance(expression, outer_types):
        return None
    return expression.args.get("limit") or expression.args.get("fetch")


def _limit_value(limit_node: Optional[exp.Expression]) -> Optional[int]:
    if limit_node is None:
        return None
    # LIMIT n 的行数在 expression 上；FETCH FIRST n ROWS ONLY 在 count 上
    candidates = [getattr(limit_node, "expression", None)]
    candidates.append((getattr(limit_node, "args", None) or {}).get("count"))
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(str(getattr(candidate, "name", candidate)))
        except (TypeError, ValueError, AttributeError):
            continue
    return None


def _render_with_limit(expression: exp.Expression, safe_limit: int, read: Optional[str]) -> Optional[str]:
    try:
        limited = expression.limit(safe_limit)
        rendered = limited.sql(dialect=read) if read else limited.sql()
    except (ParseError, ValueError):
        return None
    return rendered or None


def _clamp_outer_limit(
    base: str,
    expression: exp.Expression,
    safe_limit: int,
    read: Optional[str],
) -> str:
    """外层已有上限：本来就不超就原样返回，超了就收紧。

    只"有 LIMIT 就跳过"会放过 `LIMIT 99999999` 这种写法，等于没有封顶。
    """
    current = _limit_value(_outer_limit_node(expression))
    if current is None or current <= safe_limit:
        return base
    return _render_with_limit(expression, safe_limit, read) or base


def _text_has_outer_row_limit(sql: str) -> bool:
    """解析失败时的兜底：按 token 跟踪括号深度，只认深度 0 上的 LIMIT/FETCH。"""
    try:
        from sqlglot.tokens import Tokenizer, TokenType

        tokens = Tokenizer().tokenize(sql)
    except Exception:  # noqa: BLE001
        return False
    depth = 0
    for token in tokens:
        token_type = token.token_type
        if token_type == TokenType.L_PAREN:
            depth += 1
        elif token_type == TokenType.R_PAREN:
            depth = max(0, depth - 1)
        elif depth == 0 and token_type in (TokenType.LIMIT, TokenType.FETCH):
            return True
    return False


def _apply_sqlserver_top(base: str, safe_limit: int) -> str:
    """AST 解析失败时才用的文本兜底：在首个 SELECT 上补 TOP。"""
    if re.match(r"(?is)^\s*select\s+distinct\s+", base):
        return re.sub(
            r"(?is)^\s*select\s+distinct\s+",
            f"SELECT DISTINCT TOP {safe_limit} ",
            base,
            count=1,
        )
    return re.sub(r"(?is)^\s*select\s+", f"SELECT TOP {safe_limit} ", base, count=1)


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
    base = strip_trailing_comment_and_terminator(select_sql)
    if not base:
        return base
    safe_limit = clamp_row_limit(limit, max_limit=max_limit, min_limit=min_limit)
    dialect_lower = str(dialect or "clickhouse").lower()
    read = _read_dialect(dialect_lower)
    expression = _parse_outer(base, read)

    if "oracle" in dialect_lower:
        # 内层已有 ROWNUM/FETCH 也照样包一层：外层 ROWNUM 只会更严格，不会更宽松，
        # 而"内层有 ROWNUM 就跳过"会让外层真正无上限。
        # 右括号另起一行，万一注释没剥干净也不会把 `)` 吞掉。
        return f"SELECT * FROM (\n{base}\n) WHERE ROWNUM <= {safe_limit}"

    if any(token in dialect_lower for token in ("sqlserver", "mssql", "tsql")):
        if expression is not None:
            if _outer_limit_node(expression) is not None:
                return _clamp_outer_limit(base, expression, safe_limit, "tsql")
            rendered = _render_with_limit(expression, safe_limit, "tsql")
            if rendered:
                return rendered
        return _apply_sqlserver_top(base, safe_limit)

    # MySQL / ClickHouse / PostgreSQL 等：外层没有上限才追加，有则只收紧。
    if expression is not None:
        if _outer_limit_node(expression) is not None:
            return _clamp_outer_limit(base, expression, safe_limit, read)
        # 换行后再拼 LIMIT：即便结尾有未识别的行注释，也不会把 LIMIT 吞进注释里
        return f"{base}\nLIMIT {safe_limit}"
    if _text_has_outer_row_limit(base):
        return base
    return f"{base}\nLIMIT {safe_limit}"


def dialect_limit_hint(dialect: str) -> str:
    """人类可读的分页语法提示，用于 repair 文案。"""
    dialect_lower = str(dialect or "clickhouse").lower()
    if "oracle" in dialect_lower:
        return "ROWNUM <= N 或 FETCH FIRST N ROWS ONLY"
    if any(token in dialect_lower for token in ("sqlserver", "mssql", "tsql")):
        return "TOP N"
    return "LIMIT N"
