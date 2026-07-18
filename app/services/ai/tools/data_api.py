import logging
import httpx
import json
from typing import Any, Optional
from app.services.ai.tools.tool_compat import tool
from app.core.config import settings
import re
import asyncio

logger = logging.getLogger(__name__)

MAX_LOCAL_SQL_ROWS = 1000
MAX_LOCAL_RESULT_BYTES = 2 * 1024 * 1024

import sqlglot
from sqlglot.errors import ParseError
from sqlglot import exp

def validate_sql(sql: str, dialect: str = "clickhouse") -> Optional[str]:
    """
    Validates the input SQL string for safety and policy compliance.
    Uses sqlglot for robust syntax checking.
    Allows read-only statements: SELECT, WITH...SELECT, EXPLAIN, SHOW, DESCRIBE/DESC.
    """
    # Normalize
    sql_clean = sql.strip()
    if not sql_clean:
        return "Empty SQL query."

    # Strip leading SQL comments (/* ... */ block and -- line) before keyword check
    sql_for_check = re.sub(
        r'^(\s*/\*.*?\*/|\s*--[^\n]*\n)*\s*', '', sql_clean, flags=re.DOTALL
    )
    if not re.match(r"^(SELECT|WITH|EXPLAIN|SHOW|DESC(?:RIBE)?)\b", sql_for_check, re.IGNORECASE):
        return "Only read-only queries (SELECT, EXPLAIN, SHOW, DESCRIBE) are allowed."

    try:
        from app.services.sql_query_execution_service import to_sqlglot_dialect

        # Syntax & Structure Validation via SQLGlot
        parsed = sqlglot.parse(sql_clean, read=to_sqlglot_dialect(dialect))

        # Ensure it's not multiple statements
        if len(parsed) > 1:
            return "Multi-statement queries are prohibited."

        expression = parsed[0]

        # Block known write/DDL operation types (blacklist approach)
        _WRITE_TYPE_NAMES = ("Insert", "Update", "Delete", "Drop", "Create", "AlterTable", "Merge")
        _WRITE_TYPES = tuple(getattr(exp, n) for n in _WRITE_TYPE_NAMES if hasattr(exp, n))
        if isinstance(expression, _WRITE_TYPES):
            return "Write/DDL operations are not allowed."

        # Deep check for dangerous commands in AST
        # Allow safe read-only commands; block system commands like OPTIMIZE, KILL, etc.
        _SAFE_COMMANDS = {"EXPLAIN", "SHOW", "DESCRIBE", "DESC"}
        for node in expression.find_all(exp.Command):
            cmd_name = str(getattr(node, "this", "")).strip().upper()
            if cmd_name not in _SAFE_COMMANDS:
                return f"System command or dangerous keyword detected: {node}"

    except ParseError as e:
        # sqlglot ParseError might have different structures depending on version
        msg = str(e)
        if e.errors:
            error = e.errors[0]
            if isinstance(error, dict):
                msg = error.get("message") or error.get("description") or str(e)
        return f"{dialect.capitalize()} SQL Syntax Error: {msg}"
    except Exception as e:
        return f"SQL Validation Failed: {str(e)}"

    return None

async def call_external_sql_api(
    sql: str,
    data_source: Optional[str] = None,
    cache_scope: Optional[str] = None,
) -> str:
    """
    执行物理 SQL 查询的统一入口：支持本地 Adapter 直连与远程 API 调用的双层分流控制。

    cache_scope: 结果缓存的隔离作用域（通常为执行用户 id）。必须传入，
    否则不同用户在行级权限场景下可能复用到彼此的缓存结果，造成跨用户数据泄露。
    """
    # Dynamic Config
    from app.services.config_service import ConfigService
    from app.core.redis import get_redis
    import hashlib
    import os

    # Use provided data_source or fetch from config, fallback to 'default_clickhouse'
    if not data_source:
        data_source = await ConfigService.get("external_sql_data_source", default="default_clickhouse")

    # 1. 分流执行判定
    # 优先检测系统环境变量 SQL_EXECUTION_MODE (强制控制)
    env_mode = os.environ.get("SQL_EXECUTION_MODE", "").strip().lower()
    if env_mode in ("local", "remote"):
        execution_mode = env_mode
    else:
        # 环境变量为空或 auto 时，读取数据库动态配置 sql_execution_mode
        try:
            execution_mode = await ConfigService.get("sql_execution_mode", default="remote")
            execution_mode = execution_mode.strip().lower()
        except Exception:
            execution_mode = "remote"

        if execution_mode not in ("local", "remote"):
            execution_mode = "remote"

    timeout_str = await ConfigService.get("data_api_timeout_seconds")
    timeout = float(timeout_str) if timeout_str else 60.0

    # 2. Check Cache (TTL 60s)
    # Cache Key 必须包含执行模式（避免 local/remote 切换复用）与用户作用域（避免跨用户复用行级结果）。
    scope = str(cache_scope) if cache_scope is not None and str(cache_scope).strip() else "anon"
    cache_digest = hashlib.md5((scope + "|" + sql + "|" + (data_source or "")).encode()).hexdigest()
    cache_key = f"sql_result:{execution_mode}:{scope}:{cache_digest}"
    redis_client = await get_redis()

    if redis_client:
        cached_res = await redis_client.get(cache_key)
        if cached_res:
            logger.info(f"[Agent Debug] Cache HIT for SQL: {cache_key}")
            return cached_res

    # 3. 本地直连模式分支
    if execution_mode == "local":
        logger.info(f"[Agent Local] 开始本地直连执行 SQL (数据源: {data_source})")
        try:
            from app.services.data_adapter.factory import get_adapter
            adapter = await get_adapter(data_source)
        except ValueError as e:
            return f"[TOOL_ERROR] 本地执行错误：未找到对应的数据源配置: '{data_source}'。请检查数据源管理命名或配置是否一致。\n\n[Executed SQL]:\n{sql}"
        except Exception as e:
            return f"[TOOL_ERROR] 本地执行错误：初始化适配器失败: {str(e)}\n\n[Executed SQL]:\n{sql}"

        # SQL 安全校验拦截
        try:
            adapter._validate_sql_safety(sql)
        except Exception as e:
            return f"[TOOL_ERROR] 安全策略违规：{str(e)}\n\n[Executed SQL]:\n{sql}"

        # 强制行数限制（最大不超过 1000 行），根据数据库类型转换方言
        from app.services.ai.sql_dialect_limit import apply_dialect_row_limit
        from app.services.data_adapter.oracle import OracleAdapter
        from app.services.data_adapter.sqlserver import SQLServerAdapter

        if isinstance(adapter, OracleAdapter):
            clean_sql = sql.strip().rstrip(";")
            sql_upper = clean_sql.upper()

            rownum_match = re.search(r"\bROWNUM\s*(<=|<)\s*(\d+)", sql_upper)
            fetch_match = re.search(r"\bFETCH\s+FIRST\s+(\d+)\s+ROWS", sql_upper)

            if rownum_match:
                limit_val = int(rownum_match.group(2))
                if limit_val > MAX_LOCAL_SQL_ROWS:
                    sql_limited = clean_sql[:rownum_match.start(2)] + str(MAX_LOCAL_SQL_ROWS) + clean_sql[rownum_match.end(2):]
                else:
                    sql_limited = clean_sql
            elif fetch_match:
                limit_val = int(fetch_match.group(1))
                if limit_val > MAX_LOCAL_SQL_ROWS:
                    sql_limited = clean_sql[:fetch_match.start(1)] + str(MAX_LOCAL_SQL_ROWS) + clean_sql[fetch_match.end(1):]
                else:
                    sql_limited = clean_sql
            else:
                sql_limited = f"SELECT * FROM ({clean_sql}) WHERE ROWNUM <= {MAX_LOCAL_SQL_ROWS}"
        elif isinstance(adapter, SQLServerAdapter):
            clean_sql = sql.strip().rstrip(";")
            sql_limited = apply_dialect_row_limit(
                clean_sql,
                dialect="tsql",
                limit=MAX_LOCAL_SQL_ROWS,
                max_limit=MAX_LOCAL_SQL_ROWS,
            )
        else:
            # MySQL / ClickHouse 使用 LIMIT
            clean_sql = sql.strip().rstrip(";")

            limit_match = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
            if limit_match:
                limit_val = int(limit_match.group(1))
                if limit_val > MAX_LOCAL_SQL_ROWS:
                    sql_limited = sql[:limit_match.start(1)] + str(MAX_LOCAL_SQL_ROWS) + sql[limit_match.end(1):]
                else:
                    sql_limited = sql
            else:
                sql_limited = f"SELECT * FROM ({clean_sql}) AS _sub LIMIT {MAX_LOCAL_SQL_ROWS}"

        # 物理执行与超时保护
        try:
            res_data = await asyncio.wait_for(adapter.execute_sql(sql_limited), timeout=timeout)
            result_json = json.dumps(res_data, ensure_ascii=False)
            if len(result_json.encode("utf-8")) > MAX_LOCAL_RESULT_BYTES:
                return f"[TOOL_ERROR] 本地执行结果超过最大返回体限制 ({MAX_LOCAL_RESULT_BYTES} bytes)，请缩小查询字段或过滤条件。\n\n[Executed SQL]:\n{sql}"

            # 设置缓存
            if redis_client:
                await redis_client.set(cache_key, result_json, ex=60)

            return result_json
        except asyncio.TimeoutError:
            return f"[TOOL_ERROR] SQL 执行超时，最大允许时间: {timeout} 秒。\n\n[Executed SQL]:\n{sql}"
        except Exception as e:
            return f"[TOOL_ERROR] 本地执行 SQL 失败，错误信息: {str(e)}\n\n[Executed SQL]:\n{sql}"

    # 4. 远程 API 调用模式分支
    from app.core.http_client import GlobalHttpClient
    api_url = await ConfigService.get("external_sql_api_url")
    api_key = await ConfigService.get("external_sql_api_key")

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    payload = {
        "data_source": data_source,
        "sql": sql,
        "params": {}
    }

    logger.info(f"[Agent Remote] Calling External SQL API: {api_url} (Cached: False)")

    try:
        client = await GlobalHttpClient.get_client()
        response = await client.post(api_url, headers=headers, json=payload, timeout=timeout)

        if response.is_error:
            error_detail = response.text
            try:
                error_json = response.json()
                error_detail = error_json.get("message") or error_detail
            except:
                pass
            return f"[TOOL_ERROR] External API Error ({response.status_code}): {error_detail}\n\n[Executed SQL]:\n{sql}"

        resp_data = response.json()
        if resp_data.get("code") != 200:
            return f"[TOOL_ERROR] Error from API: {resp_data.get('message')}\n\n[Executed SQL]:\n{sql}"

        result_json = json.dumps(resp_data.get("data"), ensure_ascii=False)

        if redis_client:
            await redis_client.set(cache_key, result_json, ex=60)

        return result_json

    except httpx.HTTPStatusError as e:
        return f"[TOOL_ERROR] HTTP Error: {e.response.text}\n\n[Executed SQL]:\n{sql}"
    except Exception as e:
        return f"[TOOL_ERROR] Failed to execute SQL via External API: {str(e)}\n\n[Executed SQL]:\n{sql}"

async def call_ragflow_api(query: str, dataset_ids: list[str]) -> str:
    """
    Call RAGFlow Retrieval API to get knowledge chunks.
    API Docs: POST /api/v1/retrieval
    """
    from app.services.config_service import ConfigService
    base_url = await ConfigService.get("ragflow_api_url")
    api_key = await ConfigService.get("ragflow_api_key")

    if not base_url or not api_key:
        return "[System Config Error] RAGFlow API URL or API Key is missing."

    # Normalize URL (remove trailing slash)
    base_url = base_url.rstrip("/")
    endpoint = f"{base_url}/api/v1/retrieval"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "question": query,
        "dataset_ids": dataset_ids,
        "top_k": 5, # Default to top 5 chunks
        "vector_similarity_weight": 0.5
    }

    logger.info(f"[RAGFlow] Retrieving from {dataset_ids} for query: {query}")

    # [Debug Logging]
    masked_key = api_key[:4] + "***" if api_key and len(api_key) > 4 else "***"
    logger.info(f"[Agent Debug] RAGFlow Endpoint: {endpoint}")
    logger.info(f"[Agent Debug] RAGFlow Headers: Authorization=Bearer {masked_key}")
    logger.info(f"[Agent Debug] RAGFlow Payload: {json.dumps(payload, ensure_ascii=False)}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(endpoint, headers=headers, json=payload)

            if response.status_code != 200:
                 return f"[RAG Error] API returned {response.status_code}: {response.text}"

            res_json = response.json()
            if res_json.get("code") != 0: # RAGFlow usually uses 0 for success
                 return f"[RAG Error] Service message: {res_json.get('message')}"

            # Parse chunks (Defensive Handling)
            # Structure might be { "data": { "chunks": [...] } } OR { "data": [...] }
            data = res_json.get("data", {})
            chunks = []

            if isinstance(data, list):
                chunks = data
            elif isinstance(data, dict):
                chunks = data.get("chunks", [])

            if not chunks:
                return "No relevant knowledge found in knowledge base."

            formatted_chunks = []
            for i, chunk in enumerate(chunks, 1):
                content = chunk.get("content_with_weight") or chunk.get("content") or str(chunk)
                similarity = chunk.get('similarity', 0)
                # 在每个 Source 块前面加上相似度标识
                formatted_chunks.append(f"[置信度: {similarity:.2f}]\n{content}")

            return "\n\n".join(formatted_chunks)
    except Exception as e:
        logger.error(f"[RAGFlow] Exception: {e}")
        return f"[RAG Connection Error] {str(e)}"

def _normalize_metadata_dataset_ids(raw: Any) -> Optional[list[int]]:
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else str(raw).replace("'", "").replace('"', "").split(",")
    normalized: list[int] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        try:
            dataset_id = int(text)
        except (TypeError, ValueError):
            continue
        if dataset_id not in normalized:
            normalized.append(dataset_id)
    return normalized or None


@tool
async def get_dataset_schema(keywords: Optional[str] = None, metadata_dataset_ids: Optional[Any] = None) -> str:
    """
    Retrieves the table schema, columns, and metric definitions for available datasets.
    Call this to understand which data is available and how to query it.

    Args:
        keywords: Optional. A search term or topic (e.g., "sales", "user behavior") to find relevant data.
                 In Local Mode, this is used for local vector metadata search.
                 In RAGFlow Mode, this is used as the semantic search query.
        metadata_dataset_ids: Optional. Platform-injected dataset IDs from tool runtime config.
                 Use this only when the agent has configured get_dataset_schema to a fixed metadata dataset scope.
    """
    try:
        from app.core.orm import AsyncSessionLocal
        from app.core.context import get_current_agent_context
        from app.services.chatbi_dataset_schema_service import fetch_dataset_schema_core

        ctx = get_current_agent_context()
        user_id = ctx.user_id if ctx else None
        is_admin = ctx.is_admin if ctx else False
        api_key = ctx.api_key if ctx else None
        authorized_dataset_ids = _normalize_metadata_dataset_ids(metadata_dataset_ids)

        trace_buffer = ctx.trace_buffer if ctx else None
        if trace_buffer is not None:
            from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
            async with TraceSpanContext(
                trace_buffer=trace_buffer,
                event_type="tool_call",
                span_name="get_dataset_schema",
                tool_name="get_dataset_schema",
                tool_input={"keywords": keywords}
            ) as span:
                async with AsyncSessionLocal() as session:
                    res = await fetch_dataset_schema_core(
                        session,
                        keywords=keywords,
                        user_id=user_id,
                        is_admin=is_admin,
                        api_key=api_key,
                        authorized_dataset_ids=authorized_dataset_ids,
                    )
                    span.set_output(res)
                    return res
        else:
            async with AsyncSessionLocal() as session:
                return await fetch_dataset_schema_core(
                    session,
                    keywords=keywords,
                    user_id=user_id,
                    is_admin=is_admin,
                    api_key=api_key,
                    authorized_dataset_ids=authorized_dataset_ids,
                )

    except Exception as e:
        logger.error(f"[Tool Error] Schema Retrieval Failed: {e}", exc_info=True)
        return f"[Tool Error] Failed to retrieve metadata: {str(e)}"

@tool
async def execute_sql_query(sql: str, data_source: str, dataset_name: str) -> str:
    """
    针对指定的数据源执行只读的 SQL SELECT 查询，并在指定的数据集权限范围内进行校验。

    Args:
        sql: 要执行的 SQL SELECT 查询语句。
        data_source: 数据源标识符（如 'mysql_oa'），用于决定数据库连接和 SQL 方言。
        dataset_name: 数据集名称（如 'energy_usage'），用于权限校验。
    """
    from app.core.context import get_current_agent_context
    from app.core.orm import AsyncSessionLocal
    from app.services.sql_query_execution_service import (
        attach_permission_notice_to_json_result,
        execute_sql_query_core,
    )

    ctx = get_current_agent_context()
    user_id = ctx.user_id if ctx else None
    is_admin = bool(ctx and getattr(ctx, "is_admin", False))
    permission_notice: dict[str, Any] = {}

    async def _run_query(session):
        res = await execute_sql_query_core(
            session,
            sql=sql,
            data_source=data_source,
            dataset_name=dataset_name,
            user_id=user_id,
            user_dimensions=(ctx.user_dimensions if ctx else None) or None,
            trace_logs=None,
            api_key=ctx.api_key if ctx else None,
            agent_context=ctx,
            dry_run=None,
            is_admin=is_admin,
            permission_notice=permission_notice,
        )
        return attach_permission_notice_to_json_result(res, permission_notice)

    trace_buffer = ctx.trace_buffer if ctx else None
    if trace_buffer is not None:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
        async with TraceSpanContext(
            trace_buffer=trace_buffer,
            event_type="tool_call",
            span_name="execute_sql_query",
            tool_name="execute_sql_query",
            tool_input={"sql": sql, "data_source": data_source, "dataset_name": dataset_name}
        ) as span:
            async with AsyncSessionLocal() as session:
                res = await _run_query(session)
                span.set_output(res)
                return res
    else:
        async with AsyncSessionLocal() as session:
            return await _run_query(session)
