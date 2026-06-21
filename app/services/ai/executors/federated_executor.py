from __future__ import annotations

import json
import logging
import re
import uuid
import html
import xml.etree.ElementTree as ET
from typing import Any, AsyncGenerator, Dict, List, Optional
import pandas as pd
import duckdb

from app.core.orm import AsyncSessionLocal
from app.services.permission_service import PermissionService
from app.services.metadata_service import MetadataService
from app.services.sql_query_execution_service import execute_sql_query_core
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.prompts import DataQueryPrompts

logger = logging.getLogger(__name__)

MAX_FEDERATED_ROWS = 1000


def make_markdown_table(columns: list, rows: list) -> str:
    if not columns or not rows:
        return "无结果数据。"
    headers = [c["name"] for c in columns]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        row_str = []
        for val in r:
            s = str(val).replace("\n", " ") if val is not None else ""
            row_str.append(s)
        lines.append("| " + " | ".join(row_str) + " |")
    return "\n".join(lines)


class FederatedQueryExecutor:
    """跨数据集 DuckDB 联邦查询执行器"""

    def __init__(
        self,
        agent_runner: Any,
        schema_output: str,
        datasets: list[str],
    ):
        self.agent_runner = agent_runner
        self.schema_output = schema_output
        self.datasets = datasets
        self.user_info = agent_runner.user_info
        self.trace_buffer = agent_runner.trace_buffer

    async def execute(
        self,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from app.services.ai.runtime.agentscope.trace_context import TraceSpanContext
        
        async with TraceSpanContext(
            trace_buffer=self.trace_buffer,
            event_type="agent_execution",
            span_name="FederatedQueryExecutor",
        ) as span:
            
            # 1. 计划生成日志
            plan_log_id = f"fed_plan_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": plan_log_id,
                "title": "生成跨源联邦查询计划",
                "details": "正在分析跨数据集元数据，并编排分布式子查询计划...",
                "status": "pending",
            }
            
            try:
                llm = await AgentConfigProvider.get_configured_llm(
                    streaming=False,
                    config=getattr(self.agent_runner, "config", None),
                )
                chat_client = chat_client_from_handle(llm)
                
                # 从 schema 中提取数据集与物理数据库类型的映射，用于方言约束
                dataset_dialect_map = self._extract_dialect_map(self.schema_output)
                plan_prompt = DataQueryPrompts.build_federated_plan_prompt(
                    self.schema_output,
                    user_question,
                    dataset_dialect_map=dataset_dialect_map,
                )
                plan_messages = system_user_prompt_messages(plan_prompt, user_prompt=user_question)
                
                plan_output = await chat_client.generate_text(plan_messages)
                plan_output = plan_output.strip()
                
                # 解析 XML 执行计划
                sub_queries, join_sql = self._parse_federated_plan(plan_output)
                
                if not sub_queries:
                    raise ValueError(f"联邦计划生成失败：未能成功提取到有效的子查询节点。 LLM 输出内容:\n{plan_output}")
                if not join_sql:
                    raise ValueError(f"联邦计划生成失败：未能提取到有效的 <memory_join> 逻辑。 LLM 输出内容:\n{plan_output}")
                
                yield {
                    "type": "log",
                    "id": plan_log_id,
                    "title": "生成跨源联邦查询计划",
                    "details": f"联邦计划编排完成，共规划了 {len(sub_queries)} 个子查询。计划明细：\n{plan_output}",
                    "status": "success",
                }
                
            except Exception as e:
                logger.error("[FederatedQueryExecutor] Plan generation failed: %s", e, exc_info=True)
                yield {
                    "type": "log",
                    "id": plan_log_id,
                    "title": "生成联邦查询计划失败",
                    "details": f"错误: {str(e)}",
                    "status": "error",
                }
                yield {
                    "content": f"\n❌ 跨源联邦查询计划编排失败：{str(e)}",
                    "status": "error"
                }
                return

            # 2. 执行子查询并注册到 DuckDB
            duckdb_conn = duckdb.connect(
                database=":memory:",
                config={"enable_external_access": False},
            )
            try:
                user_id = self._current_user_id()
                is_admin = self._current_user_is_admin()
                
                async with AsyncSessionLocal() as session:
                    perm_service = PermissionService(session)
                    
                    for idx, sq in enumerate(sub_queries):
                        dataset_name = sq["dataset_name"]
                        temp_table = sq["temp_table"]
                        sub_sql = sq["sql"]
                        
                        sub_log_id = f"fed_sub_{uuid.uuid4().hex[:8]}_{idx}"
                        yield {
                            "type": "log",
                            "id": sub_log_id,
                            "title": f"执行子查询 ({dataset_name})",
                            "details": f"正在执行数据集 '{dataset_name}' 的子查询，注册为临时表 '{temp_table}'...",
                            "status": "pending",
                        }
                        
                        try:
                            # 获取数据集并校验权限
                            dataset = await MetadataService.get_dataset_by_name(session, dataset_name)
                            if not dataset:
                                raise ValueError(f"元数据中未找到指定的数据集: '{dataset_name}'。")
                            
                            if not is_admin:
                                if not user_id:
                                    raise PermissionError(f"未提供有效的用户身份，无权访问数据集: '{dataset_name}'")
                                
                                has_perm = await perm_service.check_permission(int(user_id), "metadata", str(dataset.id))
                                if not has_perm:
                                    raise PermissionError(f"无权访问数据集: '{dataset_name}' (ID: {dataset.id})")
                            
                            # 物理执行子查询
                            async with TraceSpanContext(
                                trace_buffer=self.trace_buffer,
                                event_type="tool_call",
                                span_name="execute_sql_query",
                                tool_name="execute_sql_query",
                                tool_input={"sql": sub_sql, "data_source": dataset.data_source, "dataset_name": dataset.name}
                            ) as sub_span:
                                res_str = await execute_sql_query_core(
                                    session,
                                    sql=sub_sql,
                                    data_source=dataset.data_source,
                                    dataset_name=dataset.name,
                                    user_id=int(user_id) if user_id else None,
                                    is_admin=is_admin,
                                    bypass_table_auth=False,
                                )
                                sub_span.set_output(res_str)
                            
                            if res_str.startswith("[TOOL_ERROR]") or res_str.startswith("[Validation Failed]") or res_str.startswith("[Permission Denied]"):
                                raise ValueError(res_str)
                            
                            res_data = json.loads(res_str)
                            col_names = [col["name"] for col in res_data.get("columns", [])]
                            raw_items = res_data.get("items") or res_data.get("rows") or []
                            items = self._limit_rows(raw_items)
                            limit_note = (
                                f"（原始返回 {len(raw_items)} 行，已截断到 {MAX_FEDERATED_ROWS} 行）"
                                if len(raw_items) > len(items)
                                else ""
                            )
                            
                            # 注册临时表到 DuckDB
                            df = pd.DataFrame(items, columns=col_names)
                            duckdb_conn.register(temp_table, df)
                            
                            yield {
                                "type": "log",
                                "id": sub_log_id,
                                "title": f"执行子查询 ({dataset_name})",
                                "details": f"子查询执行成功。已导入临时表 '{temp_table}' ({len(items)} 行数据){limit_note}。\nSQL:\n{sub_sql}",
                                "status": "success",
                            }
                            
                        except Exception as e:
                            logger.error(f"[FederatedQueryExecutor] Subquery execution failed on dataset {dataset_name}: {e}", exc_info=True)
                            if idx == 0:
                                yield {
                                    "type": "log",
                                    "id": sub_log_id,
                                    "title": f"执行主表子查询 ({dataset_name}) 失败",
                                    "details": f"错误信息: {str(e)}\nSQL:\n{sub_sql}",
                                    "status": "error",
                                }
                                yield {
                                    "content": f"\n❌ 跨源联邦主表子查询失败（数据集: '{dataset_name}'）：{str(e)}",
                                    "status": "error"
                                }
                                return
                            else:
                                yield {
                                    "type": "log",
                                    "id": sub_log_id,
                                    "title": f"执行子查询 ({dataset_name}) 失败，已自动降级",
                                    "details": f"警告: 关联数据集 '{dataset_name}' 的查询失败，相关字段已自动降级留空处理。错误信息: {str(e)}\nSQL:\n{sub_sql}",
                                    "status": "warning",
                                }
                                yield {
                                    "content": f"\n⚠️ 警告: 关联数据集 '{dataset_name}' 的查询失败，相关字段已自动降级留空处理。",
                                    "status": "warning"
                                }
                                
                                # 使用 sqlglot 解析失败子查询的 SQL 字段
                                col_names = []
                                try:
                                    import sqlglot
                                    from sqlglot import exp
                                    dialect = dataset_dialect_map.get(dataset_name) if dataset_dialect_map else None
                                    parsed = sqlglot.parse(sub_sql, read=dialect)
                                    if parsed:
                                        expression = parsed[0]
                                        if isinstance(expression, exp.Select):
                                            cols = []
                                            for expr in expression.expressions:
                                                alias = getattr(expr, "alias", None)
                                                name = getattr(expr, "name", None)
                                                if isinstance(expr, exp.Alias):
                                                    cols.append(expr.alias)
                                                elif isinstance(expr, exp.Column):
                                                    cols.append(expr.name)
                                                elif alias:
                                                    cols.append(alias)
                                                elif name:
                                                    cols.append(name)
                                                else:
                                                    cols.append(expr.sql(dialect))
                                            col_names = [c.strip('`"\'') for c in cols if c]
                                except Exception as parse_err:
                                    logger.warning(
                                        "[FederatedQueryExecutor] Failed to parse columns from failed subquery SQL: %s",
                                        parse_err,
                                        exc_info=True
                                    )
                                
                                df = pd.DataFrame(columns=col_names)
                                duckdb_conn.register(temp_table, df)
                                continue

                # 3. 内存 Join 联合计算
                join_log_id = f"fed_join_{uuid.uuid4().hex[:8]}"
                yield {
                    "type": "log",
                    "id": join_log_id,
                    "title": "内存联邦聚合计算",
                    "details": "正在 DuckDB 内存中对所有临时表执行多源关联与最终汇总计算...",
                    "status": "pending",
                }
                
                try:
                    join_error = self._validate_memory_join_sql(join_sql)
                    if join_error:
                        raise ValueError(join_error)
                    # 执行 DuckDB 关联 SQL
                    duck_res = duckdb_conn.execute(join_sql)
                    raw_join_rows = duck_res.fetchall()
                    join_rows = self._limit_rows(raw_join_rows)
                    columns = self._columns_from_duckdb_description(duck_res.description)
                    limit_note = (
                        f"（原始产出 {len(raw_join_rows)} 行，已截断到 {MAX_FEDERATED_ROWS} 行）"
                        if len(raw_join_rows) > len(join_rows)
                        else ""
                    )
                    
                    final_data = {
                        "columns": columns,
                        "items": [list(r) for r in join_rows]
                    }
                    
                    yield {
                        "type": "log",
                        "id": join_log_id,
                        "title": "内存联邦聚合计算",
                        "details": f"内存关联计算成功。汇总产出 {len(join_rows)} 行数据{limit_note}。\nSQL:\n{join_sql}",
                        "status": "success",
                    }
                    
                except Exception as e:
                    logger.error("[FederatedQueryExecutor] Memory join failed: %s", e, exc_info=True)
                    yield {
                        "type": "log",
                        "id": join_log_id,
                        "title": "内存联邦聚合计算失败",
                        "details": f"错误信息: {str(e)}\nSQL:\n{join_sql}",
                        "status": "error",
                    }
                    yield {
                        "content": f"\n❌ 内存联邦计算失败：{str(e)}",
                        "status": "error"
                    }
                    return

                # 4. 可视化分析与总结解读
                final_md_table = make_markdown_table(columns, join_rows)
                
                # 保存至 "上一轮数据结果" 中供下一次追问与微调
                try:
                    await self.agent_runner._save_last_data_result_for_followups(
                        {
                            "sql": join_sql,
                            "data_source": "duckdb",
                            "dataset_name": "federated"
                        },
                        final_data
                    )
                except Exception as e:
                    logger.warning("[FederatedQueryExecutor] Failed to save last data result: %s", e)

                # 调用 LLM 做最终的分析和可视化
                synthesis_prompt = DataQueryPrompts.build_federated_synthesis_prompt(user_question, final_md_table)
                
                # 流式输出总结（synthesis_prompt 已含用户问题，user_prompt 用默认值避免重复）
                llm_stream = await AgentConfigProvider.get_configured_llm(
                    streaming=True,
                    config=getattr(self.agent_runner, "config", None),
                )
                chat_client_stream = chat_client_from_handle(llm_stream)
                synthesis_messages = system_user_prompt_messages(synthesis_prompt)
                
                chunk_count = 0
                async for msg_chunk in chat_client_stream.stream_messages(synthesis_messages):
                    text = msg_chunk.content if isinstance(msg_chunk.content, str) else ""
                    if text:
                        chunk_count += 1
                        yield {"content": text}
                
                logger.info("[FederatedQueryExecutor] Synthesis complete. Total text chunks yielded: %d", chunk_count)
                yield {"content": "", "status": "success"}
            finally:
                duckdb_conn.close()

    def _current_user_id(self) -> Optional[int]:
        resolver = getattr(self.agent_runner, "_current_user_id", None)
        if callable(resolver):
            return resolver()
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    def _current_user_is_admin(self) -> bool:
        resolver = getattr(self.agent_runner, "_current_user_is_admin", None)
        if callable(resolver):
            return bool(resolver())
        if not self.user_info:
            return False
        if bool(self.user_info.get("is_admin")):
            return True
        role = str(
            self.user_info.get("role")
            or self.user_info.get("role_name")
            or ""
        ).strip().lower()
        return role in {"admin", "administrator", "平台管理员"}

    @staticmethod
    def _limit_rows(rows: list[Any], *, max_rows: int = MAX_FEDERATED_ROWS) -> list[Any]:
        if not isinstance(rows, list):
            return []
        return rows[:max_rows]

    @staticmethod
    def _validate_memory_join_sql(sql: str) -> Optional[str]:
        sql_text = str(sql or "").strip()
        if not sql_text:
            return "内存联邦 SQL 为空。"
        sql_no_comments = re.sub(r"(--[^\n]*|/\*.*?\*/)", " ", sql_text, flags=re.DOTALL)
        if ";" in sql_no_comments.rstrip(";"):
            return "内存联邦 SQL 只允许单条 SELECT 查询。"
        if not re.match(r"^(SELECT|WITH)\b", sql_no_comments.strip(), flags=re.IGNORECASE):
            return "内存联邦 SQL 只允许 SELECT/WITH 查询。"
        blocked = re.search(
            r"\b(COPY|INSTALL|LOAD|ATTACH|DETACH|EXPORT|IMPORT|CREATE|INSERT|UPDATE|DELETE|DROP|ALTER|PRAGMA|CALL|"
            r"READ_CSV|READ_CSV_AUTO|READ_PARQUET|READ_JSON|READ_JSON_AUTO|READ_TEXT|READ_BLOB|HTTPFS)\b",
            sql_no_comments,
            flags=re.IGNORECASE,
        )
        if blocked:
            return f"内存联邦 SQL 禁止外部访问或写操作: {blocked.group(1)}"
        return None

    @staticmethod
    def _duckdb_type_to_result_type(type_name: Any) -> str:
        text = str(type_name or "").upper()
        if any(token in text for token in ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "HUGEINT", "UBIGINT")):
            return "number"
        if "BOOL" in text:
            return "boolean"
        if any(token in text for token in ("DATE", "TIME")):
            return "datetime"
        return "string"

    @classmethod
    def _columns_from_duckdb_description(cls, description: Any) -> list[dict[str, str]]:
        columns: list[dict[str, str]] = []
        for desc in description or []:
            name = str(desc[0]) if desc else ""
            type_name = desc[1] if len(desc) > 1 else ""
            columns.append({"name": name, "type": cls._duckdb_type_to_result_type(type_name)})
        return columns

    @staticmethod
    def _extract_dialect_map(schema_output: str) -> dict[str, str]:
        """从合并 Schema YAML 文本中提取 dataset_name -> data_source 的方言映射。
        
        Schema 每个 table chunk 中包含相邻的两行：
          dataset: <dataset_name>
          ...
          data_source: <type>   (如 mysql, clickhouse, oracle)
        
        此函数扫描所有这样的组合并去重，返回 {dataset_name: data_source} 字典。
        """
        dialect_map: dict[str, str] = {}
        # 匹配 "dataset: xxx" 后（允许中间有若干行）紧跟着 "data_source: yyy"
        # 因为 YAML 中 dataset 和 data_source 在同一 block 内出现，逐行扫描更健壮
        lines = schema_output.splitlines()
        current_dataset: str | None = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("dataset:"):
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    current_dataset = parts[1].strip()
            elif stripped.startswith("data_source:") and current_dataset:
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    ds_type = parts[1].strip()
                    if ds_type and current_dataset not in dialect_map:
                        dialect_map[current_dataset] = ds_type
                    # 读到 data_source 后重置，避免错误归属
                    current_dataset = None
        return dialect_map

    def _parse_federated_plan(self, xml_content: str) -> tuple[list[dict], str]:
        """解析联邦 XML 执行计划，采用正则表达式容错提取"""
        sub_queries = []
        
        # 匹配 <sub_query dataset_name="..." temp_table="...">SQL</sub_query>
        sub_query_matches = re.finditer(
            r'<sub_query\s+dataset_name=["\']([^"\']+)["\']\s+temp_table=["\']([^"\']+)["\']\s*>(.*?)</sub_query>',
            xml_content,
            re.DOTALL | re.IGNORECASE
        )
        
        for m in sub_query_matches:
            ds_name = m.group(1).strip()
            temp_tb = m.group(2).strip()
            sql_content = m.group(3).strip()
            
            # 去掉 CDATA 包裹
            if sql_content.startswith("<![CDATA[") and sql_content.endswith("]]>"):
                sql_content = sql_content[9:-3].strip()
            elif sql_content.startswith("<![CDATA["):
                sql_content = sql_content[9:].strip()
            elif sql_content.endswith("]]>"):
                sql_content = sql_content[:-3].strip()
                
            # HTML 字符实体反转义
            sql_content = html.unescape(sql_content).strip()
            
            sub_queries.append({
                "dataset_name": ds_name,
                "temp_table": temp_tb,
                "sql": sql_content
            })
            
        join_match = re.search(r'<memory_join\s*>(.*?)</memory_join>', xml_content, re.DOTALL | re.IGNORECASE)
        join_sql = ""
        if join_match:
            join_sql = join_match.group(1).strip()
            if join_sql.startswith("<![CDATA[") and join_sql.endswith("]]>"):
                join_sql = join_sql[9:-3].strip()
            elif join_sql.startswith("<![CDATA["):
                join_sql = join_sql[9:].strip()
            elif join_sql.endswith("]]>"):
                join_sql = join_sql[:-3].strip()
                
            join_sql = html.unescape(join_sql).strip()
            
        return sub_queries, join_sql
