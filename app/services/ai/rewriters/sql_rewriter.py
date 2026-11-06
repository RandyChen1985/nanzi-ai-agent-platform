import logging
import sqlglot
from sqlglot import exp, parse_one
from typing import Any, Optional, Dict, List, Any, Set
import re

logger = logging.getLogger(__name__)

class SQLRewriteError(Exception):
    """Exception raised when SQL rewriting fails."""
    pass


class SQLPermissionError(SQLRewriteError):
    """权限条件无法安全覆盖整条 SQL（例如 UNION 有分支未过滤），调用方必须拒绝执行。

    与普通 ``SQLRewriteError`` 区分开：上层对语法类失败返回 ``[Validation Failed]``，
    对这类失败必须原样返回 ``[Permission Denied]`` 文案，不能降级成「语法错误」。
    """
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
            applied_scope_ids: Set[int] = set()
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
                applied_scope_ids.add(id(select_node))
            if applied_count == 0:
                logger.info(f"[SQLRewriter] No scoped filters applied for tables: {query_tables}")
                if rewrite_stats is not None:
                    rewrite_stats["applied_rule_count"] = 0
                return sql

            if rewrite_stats is not None:
                rewrite_stats["applied_rule_count"] = applied_count

            # UNION / INTERSECT / EXCEPT：只要有一个顶层分支没被注入条件，
            # 该分支就是「未过滤」的。历史实现只给命中的分支加 WHERE 就返回，
            # 于是 `orders UNION orders_archive` 的第二支原样放行（FAQ 已知限制 #2）。
            # 这里改成 fail-closed：无法保证每个分支都受同一权限约束时，拒绝执行。
            unfiltered: List[exp.Select] = []
            seen_branches: Set[int] = set()
            for set_op in expression.find_all(exp.Union, exp.Intersect, exp.Except):
                for branch in self._set_operation_branches(set_op):
                    if id(branch) in seen_branches or id(branch) in applied_scope_ids:
                        continue
                    seen_branches.add(id(branch))
                    # 只读 CTE / 子查询的顶层分支本身不直接接触物理表，
                    # 它的数据来源已经在内部被约束，不算「未过滤分支」。
                    if self._branch_reads_physical_table(branch, cte_aliases):
                        unfiltered.append(branch)
            if applied_count and unfiltered:
                raise SQLPermissionError(
                    "[Permission Denied] 行级权限无法安全覆盖整条 SQL："
                    f"UNION/集合查询中有 {len(unfiltered)} 个分支没有应用任何行级过滤条件，"
                    "直接执行会绕过数据权限。请拆成单分支查询后重试；"
                    "若确有联合查询需求，请让管理员把行级条件配置到每个分支涉及的表上。"
                )

            # 4. 生成最终SQL
            result_sql = expression.sql(dialect=self.dialect)
            
            # 5. 记录应用的条件信息
            skipped_count = len(filters) - len(relevant_filters)
            logger.info(f"[SQLRewriter] Applied {applied_count} filters, skipped {skipped_count} invalid filters")
            
            return result_sql
            
        except SQLPermissionError:
            raise
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
    def _set_operation_branches(node: Any) -> List[exp.Select]:
        """展开一个 UNION / INTERSECT / EXCEPT 的叶子分支（括号/嵌套集合运算都会拆开）。"""
        if node is None:
            return []
        if isinstance(node, exp.Subquery):
            return SQLRewriter._set_operation_branches(node.this)
        if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
            branches: List[exp.Select] = []
            for key in ("this", "expression"):
                branches.extend(SQLRewriter._set_operation_branches(node.args.get(key)))
            return branches
        if isinstance(node, exp.Select):
            return [node]
        return []

    @staticmethod
    def _direct_source_names(select: exp.Select) -> Set[str]:
        """SELECT 直接 FROM/JOIN 的数据源名（不含子查询内部的表）。"""
        names: Set[str] = set()
        # sqlglot 不同版本内部 key 有 "from" / "from_" 两种写法
        from_clause = select.args.get("from") or select.args.get("from_")
        if from_clause is not None:
            name = str(getattr(from_clause.this, "name", "") or "").strip().lower()
            if name:
                names.add(name)
        for join in select.args.get("joins") or []:
            name = str(getattr(join.this, "name", "") or "").strip().lower()
            if name:
                names.add(name)
        return names

    @classmethod
    def _branch_reads_physical_table(
        cls, select: exp.Select, cte_aliases: Set[str]
    ) -> bool:
        for name in cls._direct_source_names(select):
            if name not in cte_aliases:
                return True
        return False

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

    def _parse_condition_expression(self, condition: str) -> Any:
        """把行级条件解析成 AST；解析不了返回 ``None``（调用方走保守回退）。

        **不能用正则从字符串里猜列名**：字面量里的字母会被当成标识符，
        ``status = 'ADMIN'`` 里的 ``ADMIN`` 会命中 ``IN`` 关键字、拆出一个伪列 ``adm``，
        条件归属判定随之失败、整条行级条件被静默丢弃 —— 用户直接看到全表。
        走 AST 就没有这个问题：字面量是 ``exp.Literal``，不会被当成列。
        """
        text = str(condition or "").strip()
        if not text:
            return None
        # {user.x} 变量此时还是占位符，替换成 NULL 只是为了让语法成立
        text = re.sub(r"\{user\.\w+\}", "NULL", text)
        try:
            import sqlglot

            return sqlglot.parse_one(text, read="mysql")
        except Exception:  # noqa: BLE001 - 解析失败按未知处理
            return None

    def _extract_unqualified_columns(self, condition: str) -> Optional[Set[str]]:
        """条件里未带表前缀的字段名；无法解析时返回 ``None``（未知）。"""
        expression = self._parse_condition_expression(condition)
        if expression is None:
            return None
        from sqlglot import exp

        columns: Set[str] = set()
        for column in expression.find_all(exp.Column):
            if str(getattr(column, "table", "") or "").strip():
                continue
            name = str(column.name or "").strip().lower()
            if name:
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
            # None（解析不了）与空集都退回「单表才应用」，不猜多表归属
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
        """条件里显式带表前缀的表名（lower）；无法解析时返回空集（按无前缀处理）。"""
        expression = self._parse_condition_expression(condition)
        if expression is None:
            return set()
        from sqlglot import exp

        tables: Set[str] = set()
        for column in expression.find_all(exp.Column):
            hint = str(getattr(column, "table", "") or "").strip().strip('"`[]').lower()
            if hint:
                tables.add(hint)
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
        """条件里带表前缀的字段，其表必须在当前作用域内、字段必须真实存在。

        用 AST 而不是正则：正则会把字面量里的 ``boss@corp.com`` 拆成表 ``corp`` /
        字段 ``com``，于是这类完全合法的条件被判成「引用不可用的字段」而整条查询被拒。
        """
        if not self.table_metadata:
            return True
        expression = self._parse_condition_expression(condition)
        if expression is None:
            # 解析不了就交给后面的 parse 报错，别在这里误判
            return True
        from sqlglot import exp

        lowered_tables = {str(item).lower() for item in (query_tables or set())}
        for column in expression.find_all(exp.Column):
            hint = str(getattr(column, "table", "") or "").strip().strip('"`[]').lower()
            if not hint:
                continue
            if hint not in lowered_tables:
                logger.debug(
                    "[SQLRewriter] Field %s references table not in query: %s", hint, lowered_tables
                )
                return False
            columns = self.table_metadata.get(hint)
            if columns:
                normalized = {str(item).lower() for item in columns}
                if str(column.name or "").strip().lower() not in normalized:
                    logger.debug(
                        "[SQLRewriter] Field %s not found in table %s", column.name, hint
                    )
                    return False
        return True

    @staticmethod
    def resolve_strategy(user_id: Optional[int], roles: List[str], config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        实现分层策略解析：用户 > 角色 > 默认

        身份缺失（user_id 为 None）时不走「用户/角色/默认」三层匹配：`str(None)` 永远
        命中不了 user_overrides，roles 也取不到，最后只会落到 default_policy——而
        default_policy 允许被配置成空列表（语义是「不过滤」），于是身份缺失反而变成
        「无行级过滤」。这里显式返回拒绝条件，避免该配置组合下被越权读取。
        """
        # 「开了数据权限但这一层没配规则」一律按 ``1=0`` 处理（fail-closed）。
        # 空列表不再表示「不过滤」：那样「勾了启用、没填规则」就等于把整表放开，
        # 与产品承诺的默认兜底 1=0 相反。真要放开请关掉数据权限或改授权行 row_policy=none。
        DENY: List[Dict[str, Any]] = [{"condition": "1=0"}]

        if not config:
            return DENY

        if user_id is None:
            return DENY

        # 1. 用户例外策略（最高优先级）
        user_overrides = config.get("user_overrides", {})
        if str(user_id) in user_overrides:
            return list(user_overrides[str(user_id)] or DENY)

        # 2. 角色策略（多角色 UNION）
        role_policies = config.get("role_policies", {})
        active_rules: List[Dict[str, Any]] = []
        matched_any = False

        for r in roles:
            if r not in role_policies:
                continue
            matched_any = True
            rules = role_policies[r] or []
            # 关键：**跳过**空规则角色，而不是直接 return []。
            # 原来的写法里，只要用户多挂一个「没配规则」的角色，
            # 另一个角色配得好好的行级限制就会被整体抹掉。
            if not rules:
                continue
            active_rules.extend(rules)

        if active_rules:
            return active_rules
        if matched_any:
            # 命中的角色全是空规则 = 没有任何有效限制，按拒绝处理
            return DENY

        # 3. 默认策略（兜底）
        return list(config.get("default_policy") or DENY)
