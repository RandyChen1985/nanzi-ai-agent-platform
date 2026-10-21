import logging
import sqlglot
from sqlglot import exp, parse_one
from typing import Optional, Dict, List, Any, Set
import re

logger = logging.getLogger(__name__)

class SQLRewriteError(Exception):
    """Exception raised when SQL rewriting fails."""
    pass

class SQLRewriter:
    """
    优化版SQL重写引擎，支持字段存在性检查和智能表匹配
    """

    def __init__(self, dialect: str = "clickhouse", table_metadata: Optional[Dict[str, Set[str]]] = None):
        from app.services.sql_query_execution_service import to_sqlglot_dialect

        self.dialect = to_sqlglot_dialect(dialect)
        self.table_metadata = self._normalize_table_metadata(table_metadata or {})  # {table_name: {field1, field2, ...}}

    def set_table_metadata(self, table_metadata: Dict[str, Set[str]]):
        """设置表元数据，用于字段验证"""
        self.table_metadata = self._normalize_table_metadata(table_metadata)

    def rewrite(
        self,
        sql: str,
        filters: List[Dict[str, Any]],
        user_context: Dict[str, Any],
        is_admin: bool = False,
        rewrite_stats: Optional[Dict[str, int]] = None,
    ) -> str:
        """
        优化版SQL重写：添加字段验证和智能表匹配
        """
        if not filters or is_admin:
            return sql

        try:
            # 1. 解析SQL为AST，提取查询的表
            expression = sqlglot.parse_one(sql, read=self.dialect)
            query_tables = self._extract_query_tables(expression)
            
            # 2. 智能过滤：只应用相关的条件
            relevant_filters = self._filter_relevant_conditions(filters, query_tables)
            if not relevant_filters:
                logger.info(f"[SQLRewriter] No relevant filters found for tables: {query_tables}")
                return sql
            
            applied_count = 0
            cte_aliases = self._collect_cte_aliases(expression)

            # 3. 按 SELECT 作用域应用条件到 AST。先收集节点，避免 transform 返回新节点后剪枝子查询。
            select_nodes = list(expression.find_all(exp.Select))
            select_nodes.sort(key=self._expression_depth, reverse=True)
            for select_node in select_nodes:
                table_aliases = self._extract_select_table_aliases(select_node, cte_aliases=cte_aliases)
                scoped_filters = self._filter_relevant_conditions_for_scope(relevant_filters, table_aliases)
                if not scoped_filters:
                    continue
                processed_conditions = self._prepare_conditions_for_scope(
                    scoped_filters,
                    user_context,
                    table_aliases,
                )
                if not processed_conditions:
                    raise SQLRewriteError("No valid permission filter conditions could be applied")
                for cond in processed_conditions:
                    select_node.where(cond, append=True, copy=False)
                applied_count += len(processed_conditions)
            if applied_count == 0:
                logger.info(f"[SQLRewriter] No scoped filters applied for tables: {query_tables}")
                if rewrite_stats is not None:
                    rewrite_stats["applied_rule_count"] = 0
                return sql

            if rewrite_stats is not None:
                rewrite_stats["applied_rule_count"] = applied_count

            # 4. 生成最终SQL
            result_sql = expression.sql(dialect=self.dialect)
            
            # 5. 记录应用的条件信息
            skipped_count = len(filters) - len(relevant_filters)
            logger.info(f"[SQLRewriter] Applied {applied_count} filters, skipped {skipped_count} invalid filters")
            
            return result_sql
            
        except Exception as e:
            logger.error(f"[SQLRewriter] Failed to rewrite SQL: {e}", exc_info=True)
            raise SQLRewriteError(f"SQL transformation failed: {str(e)}")

    def _extract_query_tables(self, expression: exp.Expression) -> Set[str]:
        """从SQL AST中提取所有涉及的表名"""
        tables = set()
        
        # 直接查找所有Table节点
        for node in expression.find_all(exp.Table):
            # 获取表名的原始文本（处理引号）
            if isinstance(node.this, exp.Identifier):
                table_name = node.this.this
            else:
                table_name = str(node.this)
            
            # 移除可能的引号和大小写标准化（取决于方言，这里统一转小写进行匹配）
            table_name = table_name.strip('"`[]').lower()
            if table_name:
                tables.add(table_name)
        
        return tables

    @staticmethod
    def _normalize_table_metadata(table_metadata: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
        return {
            str(table_name).lower(): {str(field) for field in fields}
            for table_name, fields in (table_metadata or {}).items()
        }

    @staticmethod
    def _expression_depth(node: exp.Expression) -> int:
        depth = 0
        parent = node.parent
        while parent is not None:
            depth += 1
            parent = parent.parent
        return depth

    @staticmethod
    def _collect_cte_aliases(expression: exp.Expression) -> Set[str]:
        aliases: Set[str] = set()
        for with_expr in expression.find_all(exp.With):
            for cte in with_expr.expressions:
                if isinstance(cte, exp.CTE) and cte.alias:
                    aliases.add(str(cte.alias).strip('"`[]').lower())
        return aliases

    @staticmethod
    def _table_name_from_node(node: exp.Table) -> str:
        if isinstance(node.this, exp.Identifier):
            table_name = node.this.this
        else:
            table_name = str(node.this)
        return str(table_name or "").strip('"`[]').lower()

    @staticmethod
    def _table_alias_from_node(node: exp.Table) -> str:
        alias = node.alias_or_name
        return str(alias or "").strip('"`[]')

    def _extract_select_table_aliases(self, select_expr: exp.Select, *, cte_aliases: Optional[Set[str]] = None) -> Dict[str, str]:
        """提取当前 SELECT 直接 FROM/JOIN 作用域内的物理表与别名映射。"""
        aliases: Dict[str, str] = {}
        cte_aliases = cte_aliases or set()

        def add_table(node: Any) -> None:
            if not isinstance(node, exp.Table):
                return
            table_name = self._table_name_from_node(node)
            if table_name in cte_aliases:
                return
            alias_name = self._table_alias_from_node(node) or table_name
            if table_name:
                aliases[table_name] = alias_name

        from_expr = select_expr.args.get("from_")
        if isinstance(from_expr, exp.From):
            add_table(from_expr.this)

        for join_expr in select_expr.args.get("joins") or []:
            if isinstance(join_expr, exp.Join):
                add_table(join_expr.this)

        return aliases

    def _filter_relevant_conditions(self, filters: List[Dict[str, Any]], query_tables: Set[str]) -> List[Dict[str, Any]]:
        """智能过滤：只返回与查询表相关的条件"""
        relevant_filters = []
        
        # 将查询表名统一转小写以进行不区分大小写的匹配
        query_tables_lower = {t.lower() for t in query_tables}
        
        for f in filters:
            condition = f.get("condition", "")
            target_table = str(f.get("target_table", "all")).lower()
            
            # 1. 显式指定的 target_table 校验
            if target_table != "all":
                if target_table not in query_tables_lower:
                    logger.debug(f"[SQLRewriter] Skipping filter for table '{target_table}' not in query {query_tables_lower}")
                    continue
            
            # 2. 从条件中提取涉及的表名
            condition_tables = self._extract_tables_from_condition(condition)
            condition_tables_lower = {t.lower() for t in condition_tables}
            
            # 3. 逻辑判定：
            # - 如果条件没有指定表名 (如 "status=1")，默认认为相关
            # - 如果条件指定了表名，且表名在查询涉及的表中，则相关
            if not condition_tables_lower or (condition_tables_lower & query_tables_lower):
                relevant_filters.append(f)
            else:
                logger.info(f"[SQLRewriter] Skipping unrelated filter: '{condition}' (Tables: {condition_tables_lower})")
        
        return relevant_filters

    def _extract_unqualified_columns(self, condition: str) -> Set[str]:
        """从条件字符串中提取未带表前缀的字段名。"""
        clean_condition = re.sub(r"\{user\.\w+\}", "", condition)
        without_qualified = re.sub(
            r'(?:["`\[])?[a-zA-Z_][a-zA-Z0-9_]*(?:["`\]])?\.[a-zA-Z_][a-zA-Z0-9_]*',
            "",
            clean_condition,
        )
        reserved = {
            "and", "or", "not", "in", "is", "like", "null", "true", "false",
            "between", "exists", "case", "when", "then", "else", "end",
        }
        columns: Set[str] = set()
        for match in re.finditer(
            r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|<>|!=|<|>|<=|>=|IN\b|LIKE\b|IS\b|BETWEEN\b)",
            without_qualified,
            flags=re.IGNORECASE,
        ):
            name = str(match.group(1) or "").lower()
            if name and name not in reserved:
                columns.add(name)
        return columns

    def _local_tables_for_unqualified_condition(
        self,
        condition: str,
        local_tables: Set[str],
    ) -> Set[str]:
        """判定无表前缀条件在当前 SELECT 作用域内可安全应用的物理表。"""
        if not local_tables:
            return set()
        unqualified_columns = self._extract_unqualified_columns(condition)
        if not unqualified_columns:
            return local_tables if len(local_tables) == 1 else set()
        if not self.table_metadata:
            return local_tables if len(local_tables) == 1 else set()

        matching_tables = {
            table_name
            for table_name in local_tables
            if unqualified_columns.issubset(self.table_metadata.get(table_name, set()))
        }
        return matching_tables if len(matching_tables) == 1 else set()

    def _filter_relevant_conditions_for_scope(
        self,
        filters: List[Dict[str, Any]],
        table_aliases: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """只返回适用于当前 SELECT 直接表作用域的条件。"""
        local_tables = set(table_aliases.keys())
        if not local_tables:
            return []
        relevant_filters = []

        for f in filters:
            condition = f.get("condition", "")
            target_table = str(f.get("target_table", "all")).strip('"`[]').lower()
            condition_tables = self._extract_tables_from_condition(condition)

            if target_table != "all" and target_table not in local_tables:
                continue
            if condition_tables and not condition_tables.issubset(local_tables):
                continue
            if not condition_tables:
                if target_table != "all":
                    if target_table in local_tables:
                        relevant_filters.append(f)
                    continue
                if self._local_tables_for_unqualified_condition(condition, local_tables):
                    relevant_filters.append(f)
                continue
            relevant_filters.append(f)

        return relevant_filters

    def _extract_tables_from_condition(self, condition: str) -> Set[str]:
        """从条件字符串中提取表名"""
        tables = set()
        
        # 1. 首先移除所有 {user.xxx} 占位符，防止误匹配
        clean_condition = re.sub(r'\{user\.\w+\}', '', condition)
        
        # 2. 匹配 tablename.fieldname 或 "tablename".fieldname 或 [tablename].fieldname 模式
        table_field_pattern = r'(?:["`\[])?([a-zA-Z_][a-zA-Z0-9_]*)(?:["`\]])?\.[a-zA-Z_][a-zA-Z0-9_]*'
        matches = re.findall(table_field_pattern, clean_condition)
        
        for m in matches:
            tables.add(m.lower())
        
        return tables

    def _prepare_conditions(self, filters: List[Dict[str, Any]], context: Dict[str, Any]) -> List[exp.Expression]:
        """兼容旧版测试的方法名"""
        return self._prepare_conditions_with_validation(filters, context, set())

    def _prepare_conditions_with_validation(
        self, 
        filters: List[Dict[str, Any]], 
        context: Dict[str, Any],
        query_tables: Set[str]
    ) -> List[exp.Expression]:
        """处理条件并验证字段存在性"""
        prepared = []
        
        for f in filters:
            cond_str = f.get("condition")
            if not cond_str:
                raise SQLRewriteError("Permission filter condition is empty")
            
            # 1. 替换用户变量
            temp_cond = self._replace_user_variables(cond_str, context)
            
            # 2. 验证字段存在性 (只有在提供了 query_tables 时才验证)
            if not query_tables or self._validate_condition_fields(temp_cond, query_tables):
                try:
                    cond_expr = sqlglot.parse_one(temp_cond, read=self.dialect)
                    prepared.append(cond_expr)
                    logger.debug(f"[SQLRewriter] Applied condition: {temp_cond}")
                except Exception as e:
                    logger.error(f"[SQLRewriter] Failed to parse permission condition '{temp_cond}': {e}")
                    raise SQLRewriteError(f"Permission filter condition parse failed: {e}") from e
            else:
                logger.error(f"[SQLRewriter] Invalid permission condition due to field validation: {temp_cond}")
                raise SQLRewriteError(f"Permission filter condition references unavailable fields: {temp_cond}")
        
        return prepared

    def _prepare_conditions_for_scope(
        self,
        filters: List[Dict[str, Any]],
        context: Dict[str, Any],
        table_aliases: Dict[str, str],
    ) -> List[exp.Expression]:
        prepared = []
        query_tables = set(table_aliases.keys())

        for f in filters:
            cond_str = f.get("condition")
            if not cond_str:
                raise SQLRewriteError("Permission filter condition is empty")

            temp_cond = self._replace_user_variables(cond_str, context)
            if not query_tables or not self._validate_condition_fields(temp_cond, query_tables):
                logger.error(f"[SQLRewriter] Invalid permission condition due to field validation: {temp_cond}")
                raise SQLRewriteError(f"Permission filter condition references unavailable fields: {temp_cond}")

            try:
                cond_expr = sqlglot.parse_one(temp_cond, read=self.dialect)
                self._rewrite_condition_table_aliases(cond_expr, table_aliases)
                prepared.append(cond_expr)
                logger.debug(f"[SQLRewriter] Applied condition: {temp_cond}")
            except Exception as e:
                logger.error(f"[SQLRewriter] Failed to parse permission condition '{temp_cond}': {e}")
                raise SQLRewriteError(f"Permission filter condition parse failed: {e}") from e

        return prepared

    @staticmethod
    def _rewrite_condition_table_aliases(condition: exp.Expression, table_aliases: Dict[str, str]) -> None:
        for column in condition.find_all(exp.Column):
            table_name = str(column.table or "").strip('"`[]').lower()
            alias = table_aliases.get(table_name)
            if alias and alias != table_name:
                column.set("table", exp.to_identifier(alias))

    def _replace_user_variables(self, condition: str, context: Dict[str, Any]) -> str:
        """替换用户变量占位符

        支持两类值：
        - 标量（字符串 / 数字）：直接代入，字符串自动加引号。`{user.dept_code}` 走这里，
          对应主部门，旧规则零改动；
        - 序列：渲染成 `'A', 'B'`（**不带括号**），供模板写成 `IN ({user.dept_codes})`。
          空序列渲染成 `NULL`——`IN ()` 是语法错误，而"没有任何部门"的正确语义是
          条件恒假：既不能放行，也不能让整条查询炸掉。
        """
        placeholders = re.findall(r"\{user\.(\w+)\}", condition)
        temp_cond = condition

        for key in placeholders:
            val = context.get(key)

            if isinstance(val, (list, tuple, set, frozenset)):
                rendered = self._render_sql_value_list(val)
                # 列表也要容错外层引号：管理员可能照着标量的写法写成
                # `dept_code IN ('{user.dept_codes}')`，直接替换会得到
                # `IN (''A', 'B'')` 这种语法错误，排查起来极费劲。
                quoted_list = f"'{{user.{key}}}'"
                if quoted_list in temp_cond:
                    temp_cond = temp_cond.replace(quoted_list, rendered)
                else:
                    temp_cond = temp_cond.replace(f"{{user.{key}}}", rendered)
            elif isinstance(val, str):
                pattern_with_quotes = f"'{{user.{key}}}'"
                safe_val = self._quote_sql_string_value(val)
                if pattern_with_quotes in temp_cond:
                    temp_cond = temp_cond.replace(f"{{user.{key}}}", safe_val)
                else:
                    temp_cond = temp_cond.replace(f"{{user.{key}}}", f"'{safe_val}'")
            else:
                repl_val = str(val) if val is not None else "NULL"
                temp_cond = temp_cond.replace(f"{{user.{key}}}", repl_val)
        
        return temp_cond

    def _render_sql_value_list(self, values) -> str:
        """把一组值渲染成 `'A', 'B'`；去重保序、过滤空白、转义单引号。"""
        seen = set()
        rendered: List[str] = []
        for raw in values:
            text = str(raw).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            rendered.append(f"'{self._quote_sql_string_value(text)}'")
        return ", ".join(rendered) if rendered else "NULL"

    @staticmethod
    def _quote_sql_string_value(value: str) -> str:
        return str(value).replace("'", "''")

    def _validate_condition_fields(self, condition: str, query_tables: Set[str]) -> bool:
        """验证条件中的字段是否在查询表中存在"""
        if not self.table_metadata:
            # 如果没有元数据，跳过验证
            return True
        
        # 提取条件中的所有字段引用
        field_pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_]*)'
        matches = re.findall(field_pattern, condition)
        
        if not matches:
            # 如果没有明确的表.字段格式，可能是无表前缀的字段，暂时允许通过
            return True
        
        for table_name, field_name in matches:
            table_key = str(table_name).lower()
            field_key = str(field_name)
            # 检查表是否在查询中
            if table_key not in query_tables:
                logger.debug(f"[SQLRewriter] Field {table_name}.{field_name} references table not in query: {query_tables}")
                return False

            # 检查字段是否在表中存在
            if table_key in self.table_metadata:
                if field_key not in self.table_metadata[table_key]:
                    logger.debug(f"[SQLRewriter] Field {field_name} not found in table {table_name}")
                    return False
        
        return True

    @staticmethod
    def resolve_strategy(user_id: int, roles: List[str], config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        实现分层策略解析：用户 > 角色 > 默认
        """
        if not config:
            return []

        # 1. 用户例外策略（最高优先级）
        user_overrides = config.get("user_overrides", {})
        if str(user_id) in user_overrides:
            return user_overrides[str(user_id)]

        # 2. 角色策略（多角色UNION）
        role_policies = config.get("role_policies", {})
        active_rules = []
        matched_any = False
        
        for r in roles:
            if r in role_policies:
                matched_any = True
                rules = role_policies[r]
                # 如果任何角色是admin或有空规则（完全访问），返回空
                if not rules:
                    return []
                active_rules.extend(rules)
        
        if matched_any:
            return active_rules

        # 3. 默认策略（兜底）
        return config.get("default_policy", [{"condition": "1=0"}])
