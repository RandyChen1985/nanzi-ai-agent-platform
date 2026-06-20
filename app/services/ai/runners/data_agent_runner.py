from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
import ast
from dataclasses import dataclass, field, replace
import inspect
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from agentscope.agent import Agent, ReActConfig

from app.schemas.agent import AgentExecutionStep, ChatConfig
from app.services.ai.data_query_turn_classifier import (
    DataQueryTurnType,
    data_query_turn_type_label,
    history_shows_recent_data_result,
    resolve_data_query_turn_classification,
)
from app.services.ai.config import AgentConfigProvider
from app.services.ai.executors.base import BaseExecutor
from app.services.ai.executors.common import (
    convert_history_to_messages,
    extract_tokens_from_message,
    normalize_messages_for_llm,
)
from app.services.ai.chatbi_sql_user_messages import map_sql_tool_error_for_user
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.time_anchor import build_data_query_time_anchor_block
from app.services.ai.multimodal_support import (
    ensure_multimodal_compatible,
    resolve_runtime_model_name,
)
from app.services.ai.runtime.agentscope.chat import (
    chat_client_from_handle,
    compat_to_runtime_messages,
    to_agentscope_messages,
)
from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage, system_user_prompt_messages
from app.services.ai.runtime.agentscope.agent_runtime import (
    build_model_config,
    build_tools_fingerprint,
    load_context_config,
)
from app.services.ai.runtime.agentscope.event_stream import (
    is_interrupt_sse_chunk,
    map_standard_agentscope_event,
)
from app.services.ai.runtime.agentscope.stream_reconcile import (
    finalize_visible_reply,
    truncate_for_context,
)
from app.services.ai.runtime.agentscope.session_lock import (
    SessionLockTimeout,
    agentscope_session_lock,
)
from app.services.ai.runtime.agentscope.state_store import agent_state_store
from app.services.ai.runtime.agentscope.workspace import get_local_workspace
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage, SystemMessage
from app.services.ai.runtime.agentscope.data_runtime import DATA_QUERY_MAX_STEPS_CAP
from app.services.ai.runtime.agentscope.data_tools import build_chatbi_toolkit
from app.services.ai.runtime.agentscope.tools import (
    RuntimeToolSpec,
    build_toolkit,
    runtime_tool_spec_from_legacy_tool,
)
from app.services.ai.runtime.tool_loop_detector import ToolLoopDetector
from app.services.ai.tools.registry import ToolRegistry
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)

SCHEMA_GATE_PREFIX = "[SCHEMA_GATE]"
SQL_REPEAT_GATE_PREFIX = "[SQL_REPEAT_GATE]"
SQL_STATIC_GATE_PREFIX = "[SQL_STATIC_GATE]"
FAILED_SQL_REPEAT_GATE_PREFIX = "[FAILED_SQL_REPEAT_GATE]"
SQL_PLAN_GATE_PREFIX = "[SQL_PLAN_GATE]"
TOOL_LOOP_FUSE_THRESHOLD = 3
FAILED_SQL_REPEAT_THRESHOLD = 2
DELAY_SECONDS_EXTREME_THRESHOLD = 7 * 24 * 60 * 60
MAX_DATA_REPAIR_ROUNDS = 2
DATA_REPAIR_BUDGETS = {
    "sql_before_schema": 1,
    "schema_miss": 1,
    "schema_refinement": 1,
    "schema_ambiguous": 1,
    "sql_plan_missing": 1,
    "sql_static_risk": 1,
    "sql_error": 5,
    "failed_sql_repeat": 1,
    "schema_refresh_after_sql_error": 2,
    "empty_sql_result": 1,
    "ratio_anomaly": 1,
    "duration_anomaly": 1,
    "tool_loop_fuse": 0,
    "diagnostic_sql_pending_final": 1,
    "missing_schema": 1,
    "missing_sql": 2,
}
SCHEMA_RETRY_STOPWORDS = (
    "帮我",
    "请",
    "查询",
    "查",
    "看",
    "看看",
    "统计",
    "分析",
    "一下",
    "所有",
    "全部",
    "列表",
    "清单",
    "今天",
    "昨天",
    "前天",
    "本周",
    "上周",
    "本月",
    "上月",
    "最近",
)
SCHEMA_RETRY_SUFFIXES = ("列表", "清单", "明细", "统计")
_SQL_RESULT_DISPLAY_MAX_ROWS = 15
_SQL_RESULT_ROW_KEYS = ("items", "rows", "data", "records")
_SQL_TOOL_RESULT_DELIMITER = "--- 结果 ---"
_SQL_TOOL_ERROR_DELIMITER = "--- 错误 ---"


class _ForcedFirstToolChoiceModel:
    """Inject tool_choice on the first model call of a repair round."""

    def __init__(self, inner: Any, tool_choice: Any):
        self._inner = inner
        self._tool_choice = tool_choice
        self._consumed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self._consumed and self._tool_choice is not None:
            kwargs["tool_choice"] = self._tool_choice
            self._consumed = True
        result = self._inner(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass
class _DataRunState:
    tool_names: dict[str, str] = field(default_factory=dict)
    tool_args_text: dict[str, str] = field(default_factory=dict)
    tool_outputs: dict[str, str] = field(default_factory=dict)
    tool_started_at: dict[str, float] = field(default_factory=dict)
    full_content: str = ""
    blocked_content: str = ""
    content_emitted: bool = False
    schema_completed: bool = False
    schema_service_unavailable: bool = False
    rag_not_synced: bool = False
    sql_completed: bool = False
    sql_before_schema: bool = False
    schema_miss: bool = False
    schema_needs_refinement: bool = False
    schema_ambiguous: bool = False
    schema_ambiguous_reason: str = ""
    no_authorized_schema: bool = False
    empty_sql_result: bool = False
    empty_sql_reason: str = ""
    expecting_final_sql_after_diagnostic: bool = False
    diagnostic_sql_pending_final: bool = False
    sql_error: bool = False
    sql_error_message: str = ""
    sql_fatal_error: bool = False
    sql_fatal_message: str = ""
    sql_fatal_emitted: bool = False
    sql_static_risk: bool = False
    sql_static_risk_reason: str = ""
    sql_repeat_gate_block: bool = False
    failed_sql_repeat_gate_block: bool = False
    requires_sql_plan: bool = False
    requires_sql_query: bool = True
    sql_plan_seen: bool = False
    sql_plan_missing: bool = False
    requires_fresh_data: bool = True
    text_window: str = ""
    start_synthesis: float = field(default_factory=time.time)
    ignore_text_block: bool = False
    active_text_block_id: str = ""
    text_blocks_emitted_since_last_tool: int = 0
    current_text_block_emitted: bool = False
    halt_current_react: bool = False
    last_successful_sql_output: Any = None
    successful_sqls: dict[str, Any] = field(default_factory=dict)
    ratio_anomaly: bool = False          # SQL 结果含超阈值比率（>1.5 或负值），触发对账修复
    ratio_anomaly_reason: str = ""       # 异常说明，用于修复提示词
    duration_anomaly: bool = False       # 时延/时长/时间差字段结果明显反常，触发 SQL 复核
    duration_anomaly_reason: str = ""
    tool_call_signatures: dict[str, int] = field(default_factory=dict)
    tool_loop_detector: ToolLoopDetector = field(default_factory=ToolLoopDetector)
    tool_loop_fuse_triggered: bool = False
    tool_loop_fuse_reason: str = ""
    schema_miss_count: int = 0           # 累计 schema_miss 次数（含 prefetch + ReAct 内）
    repair_attempts: dict[str, int] = field(default_factory=dict)
    last_schema_keywords: str = ""
    last_schema_tool_keywords: str = ""  # 最近一次 get_dataset_schema 实际使用的 keywords（日志用）
    controlled_schema_retry_keywords: str = ""
    last_applied_schema_retry_keywords: str = ""
    pending_schema_retry: bool = False   # 修复轮受控重试标记（不随 _reset_state_for_repair 清除）
    followup_data_saved: bool = False    # 本轮是否已将结构化 SQL 结果写入 last_data_result
    schema_table_columns: dict[str, list[str]] = field(default_factory=dict)
    failed_sql_signatures: dict[str, int] = field(default_factory=dict)
    last_failed_sql_normalized: str = ""
    last_sql_error_summary: str = ""
    schema_refresh_required: bool = False
    schema_refreshed_after_sql_error: bool = False

    @property
    def has_successful_nonempty_sql(self) -> bool:
        if not self.requires_fresh_data:
            return False
        return (
            self.schema_completed
            and self.sql_completed
            and not self.sql_before_schema
            and not self.sql_error
            and not self.empty_sql_result
        )

    @property
    def ready_to_answer(self) -> bool:
        if self.tool_loop_fuse_triggered:
            return False
        if not self.requires_fresh_data:
            return True
        if not self.requires_sql_query:
            return (
                self.schema_completed
                and not self.schema_service_unavailable
                and not self.no_authorized_schema
                and not self.rag_not_synced
                and self.schema_miss_count < 2
            )
        if self.sql_fatal_error:
            return True
        if self.schema_ambiguous and not self.sql_before_schema and not self.sql_error:
            return True
        return (
            self.schema_completed
            and self.sql_completed
            and not self.sql_before_schema
            and not self.sql_static_risk
            and not self.sql_error
            and not self.empty_sql_result
            and not self.diagnostic_sql_pending_final
            and not self.ratio_anomaly
            and not self.duration_anomaly
            and not self.tool_loop_fuse_triggered
        )


class DataAgentRunner(BaseExecutor):
    """AgentScope-native runner foundation for ChatBI/DataExecutor migration."""

    def __init__(
        self,
        config: ChatConfig,
        trace_id: str,
        trace_buffer: List[AgentExecutionStep],
        debug_options: Dict[str, Any] = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        permission_options: Dict[str, Any] = None,
    ):
        super().__init__(config, trace_id, trace_buffer, debug_options, user_info, conversation_id, permission_options)
        self._last_run_state: _DataRunState | None = None
        self.turn_classification = None
        self.intent_info = None
        self.intent_elapsed_ms = 0.0
        self._requires_fresh_data = True
        self._pending_few_shot_log: Optional[Dict[str, Any]] = None
        self._fewshot_examples: List[Dict[str, Any]] = []
        self._standalone_query = ""
        self._schema_search_keywords = ""
        self._schema_similarity_threshold: float | None = None
        self._requires_sql_query = True

    async def _ensure_schema_similarity_threshold(self) -> float:
        """与 fetch_dataset_schema_core 共用 ragflow_similarity_threshold。"""
        if self._schema_similarity_threshold is not None:
            return self._schema_similarity_threshold
        from app.services.config_service import ConfigService

        try:
            self._schema_similarity_threshold = float(
                await ConfigService.get("ragflow_similarity_threshold") or 0.2
            )
        except (TypeError, ValueError):
            self._schema_similarity_threshold = 0.2
        return self._schema_similarity_threshold

    @staticmethod
    def _schema_strong_confidence_threshold(similarity_threshold: float) -> float:
        """Schema 置信度达到配置的 ragflow_similarity_threshold 即可进入 SQL。"""
        return float(similarity_threshold)

    def _is_sql_plan_enabled(self) -> bool:
        raw = self.debug_options.get("enable_sql_plan")
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _extract_schema_confidence_values(tool_output: Any) -> list[float]:
        from app.services.schema_chunk_format import extract_schema_confidence_values
        return extract_schema_confidence_values(tool_output)

    def _runtime_agent_name(self) -> str:
        return self.config.agent_name or "DataAgent"

    def _runtime_user_id(self) -> str | None:
        user_id = self._current_user_id()
        return str(user_id) if user_id is not None else None

    def _runner_context(self, *, system_content: str, max_steps: int) -> Dict[str, Any]:
        return {
            "runner_type": "data",
            "config": self.config.model_dump(),
            "debug_options": self.debug_options,
            "permission_options": self.permission_options,
            "system_content": system_content,
            "max_steps": max_steps,
            "standalone_query": self._standalone_query,
            "schema_search_keywords": self._schema_search_keywords,
            "requires_fresh_data": self._requires_fresh_data,
            "requires_sql_query": bool(getattr(self, "_requires_sql_query", True)),
        }

    @classmethod
    def from_runner_context(
        cls,
        *,
        runner_context: Dict[str, Any],
        trace_id: str,
        trace_buffer: List[AgentExecutionStep] | None = None,
        user_info: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
    ) -> "DataAgentRunner":
        config = ChatConfig(**runner_context["config"])
        runner = cls(
            config=config,
            trace_id=trace_id,
            trace_buffer=trace_buffer or [],
            debug_options=runner_context.get("debug_options"),
            permission_options=runner_context.get("permission_options"),
            user_info=user_info,
            conversation_id=conversation_id,
        )
        runner._standalone_query = str(runner_context.get("standalone_query") or "")
        runner._schema_search_keywords = str(runner_context.get("schema_search_keywords") or "")
        runner._requires_fresh_data = bool(runner_context.get("requires_fresh_data", True))
        runner._requires_sql_query = bool(runner_context.get("requires_sql_query", True))
        return runner

    @staticmethod
    def _latest_user_runtime_messages(runtime_messages: List[Any]) -> List[Any]:
        for message in reversed(runtime_messages):
            if isinstance(message, HumanMessage):
                return [message]
        return []

    def _current_user_id(self) -> Optional[int]:
        if not self.user_info:
            return None
        raw_user_id = self.user_info.get("user_id") or self.user_info.get("id")
        if not raw_user_id:
            return None
        try:
            return int(raw_user_id)
        except (TypeError, ValueError):
            return None

    async def _resolve_max_steps(self) -> int:
        max_steps_str = await ConfigService.get("agent_max_iterations")
        raw_max = int(max_steps_str) if max_steps_str else 6
        return min(raw_max, DATA_QUERY_MAX_STEPS_CAP)

    async def _load_last_data_result(self) -> Optional[Dict[str, Any]]:
        if not self.conversation_id:
            return None
        user_id = self._current_user_id()
        if not user_id:
            return None
        try:
            from app.services.ai.memory_service import memory_service

            return await memory_service.get_last_data_result(user_id, self.conversation_id)
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to load last data result: %s", e)
            return None

    async def _load_last_data_result_with_retry(
        self,
        *,
        attempts: int = 3,
        delay_seconds: float = 0.15,
    ) -> Optional[Dict[str, Any]]:
        for attempt in range(attempts):
            result = await self._load_last_data_result()
            if result:
                return result
            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
        return None

    @staticmethod
    def _normalize_rows_for_followup_save(parsed_tool_output: Any) -> Any:
        if isinstance(parsed_tool_output, list):
            return parsed_tool_output
        if isinstance(parsed_tool_output, dict):
            return parsed_tool_output
        return None

    @staticmethod
    def _latest_data_assistant_excerpt(history: List[Dict[str, str]], *, max_chars: int = 12000) -> str:
        for msg in reversed(history[:-1] or history):
            if msg.get("role") != "assistant":
                continue
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            if len(content) > max_chars:
                return content[:max_chars] + "\n... [对话展示过长已截断]"
            return content
        return ""

    async def _save_last_data_result_for_followups(
        self,
        tool_args: Dict[str, Any],
        parsed_tool_output: Any,
    ) -> None:
        normalized = self._normalize_rows_for_followup_save(parsed_tool_output)
        if not self.conversation_id or normalized is None:
            return
        user_id = self._current_user_id()
        if not user_id:
            return

        payload = {
            "sql": tool_args.get("sql") or tool_args.get("query"),
            "data_source": tool_args.get("data_source"),
            "dataset_name": tool_args.get("dataset_name"),
            "rows": normalized,
            "saved_at": datetime.now().isoformat(),
            "trace_id": self.trace_id,
        }
        try:
            from app.services.ai.memory_service import memory_service

            await memory_service.set_last_data_result(user_id, self.conversation_id, payload)
            state = self._last_run_state
            if state is not None:
                state.followup_data_saved = True
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to save last data result: %s", e)

    def resolve_has_data_output(self) -> bool:
        """本轮 ChatBI 是否产出了可复用的结构化查数结果（供前端展示「可视化分析」入口）。"""
        state = self._last_run_state
        if not state or not state.requires_fresh_data:
            return False
        return bool(state.followup_data_saved)

    async def _resolve_runtime_tools_from_config(self) -> list[RuntimeToolSpec]:
        _, specs = await build_chatbi_toolkit(self.config.tools)
        tools = list(specs)
        seen = {spec.name for spec in tools}

        system_tools = ToolRegistry.get_system_implicit_tools()
        if system_tools:
            for tool in system_tools:
                spec = runtime_tool_spec_from_legacy_tool(tool, source_type="system")
                if spec.name in seen:
                    continue
                tools.append(spec)
                seen.add(spec.name)
        return tools

    @staticmethod
    def _is_schema_gate_block(output: Any) -> bool:
        return str(output or "").startswith(SCHEMA_GATE_PREFIX)

    @staticmethod
    def _is_sql_repeat_gate_block(output: Any) -> bool:
        return str(output or "").startswith(SQL_REPEAT_GATE_PREFIX)

    @staticmethod
    def _is_sql_static_gate_block(output: Any) -> bool:
        return str(output or "").startswith(SQL_STATIC_GATE_PREFIX)

    @staticmethod
    def _is_failed_sql_repeat_gate_block(output: Any) -> bool:
        return str(output or "").startswith(FAILED_SQL_REPEAT_GATE_PREFIX)

    @staticmethod
    def _is_sql_plan_gate_block(output: Any) -> bool:
        return str(output or "").startswith(SQL_PLAN_GATE_PREFIX)

    @staticmethod
    def _is_sql_schema_preflight_error(output: Any) -> bool:
        return str(output or "").startswith("[TOOL_ERROR] SQL 预检失败")

    @staticmethod
    def _normalize_sql_text(sql: str) -> str:
        return " ".join(str(sql or "").strip().lower().split())

    @staticmethod
    def _is_schema_reference_sql_error(message: str) -> bool:
        """字段/表引用类 SQL 错误：需重查 Schema 后再改 SQL。"""
        err = str(message or "").lower()
        if not err.strip():
            return False
        patterns = (
            r"unknown column",
            r"unknown table",
            r"invalid column",
            r"invalid field",
            r"bad field",
            r"no such column",
            r"no such table",
            r"column .+ does not exist",
            r"column not found",
            r"undefined column",
            r"invalid identifier",
            r"unresolved column",
            r"table .+ doesn't exist",
            r"table .+ does not exist",
        )
        return any(re.search(pattern, err) for pattern in patterns)

    @staticmethod
    def _extract_failed_repeat_original_error(output: Any) -> str:
        text = str(output or "").strip()
        for marker in ("上次错误摘要：", "上次错误摘要:"):
            if marker in text:
                return text.rsplit(marker, 1)[1].strip()[:800]
        return ""

    @staticmethod
    def _extract_invalid_sql_identifiers(message: str) -> list[str]:
        text = str(message or "")
        if not text.strip():
            return []
        candidates: list[str] = []
        patterns = (
            r"ORA-\d+:\s*(?:\"[^\"]+\"\.)?\"([^\"]+)\"\s*:\s*invalid identifier",
            r"unknown column\s+['\"]([^'\"]+)['\"]",
            r"no such column:\s*([A-Za-z_][A-Za-z0-9_.$]*)",
            r"column\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?\s+does not exist",
            r"invalid identifier\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?",
            r"unresolved column\s+['\"]?([A-Za-z_][A-Za-z0-9_.$]*)['\"]?",
        )
        for pattern in patterns:
            candidates.extend(re.findall(pattern, text, flags=re.IGNORECASE))

        identifiers: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if isinstance(candidate, tuple):
                candidate = next((part for part in candidate if part), "")
            value = str(candidate or "").strip().strip('"').strip("'")
            if not value:
                continue
            key = value.lower()
            if key in seen:
                continue
            seen.add(key)
            identifiers.append(value)
            if len(identifiers) >= 8:
                break
        return identifiers

    def _invalid_identifier_repair_hint(self, message: str) -> str:
        identifiers = self._extract_invalid_sql_identifiers(message)
        if not identifiers:
            return ""
        return (
            "\n\n【无效标识符定位】本次数据库明确报出的无效字段/标识符："
            f"{', '.join(identifiers)}。请在 get_dataset_schema 返回的物理列名中逐项核对；"
            "若 schema 未列出这些字段，必须删除或替换为真实物理列，"
            "并同步修正 SELECT、JOIN、WHERE、GROUP BY、ORDER BY 中所有引用，"
            "不得继续使用这些字段名。"
        )

    def _sql_repair_taxonomy_hint(self, message: str) -> str:
        text = str(message or "")
        lower = text.lower()
        if self._is_date_format_sql_error(text):
            category = "date_format"
            focus = "核对日期字段类型、日期字面量格式、TO_DATE/TO_CHAR 或时间边界表达式"
        elif self._is_schema_reference_sql_error(text):
            category = "invalid_identifier"
            focus = "核对字段名、表名或别名引用"
        elif "not a group by" in lower or "group by expression" in lower or "ora-00979" in lower:
            category = "group_by_mismatch"
            focus = "核对 SELECT 中非聚合字段是否全部进入 GROUP BY，或改为聚合表达式"
        elif "join" in lower and ("cartesian" in lower or "missing" in lower or "condition" in lower):
            category = "join_condition_missing"
            focus = "补齐 JOIN ON 条件，并确认左右表关联键来自 Schema"
        elif "permission" in lower or "unauthorized" in lower or "access denied" in lower:
            category = "permission_denied"
            focus = "不要改写 SQL 绕过权限，应如实说明权限不足或请求授权"
        elif "syntax" in lower or "unexpected token" in lower or "invalid expression" in lower:
            category = "syntax_error"
            focus = "修正数据库方言语法、分页写法、函数名和括号结构"
        else:
            category = "sql_execution_error"
            focus = "根据数据库错误信息最小化修改 SQL，禁止无依据更换业务口径"
        return f"\n\n【SQL Repair Taxonomy】错误分类：{category}\n修复重点：{focus}。"

    @staticmethod
    def _normalize_sql_identifier(identifier: str) -> str:
        value = str(identifier or "").strip()
        value = value.strip('"').strip("`").strip("[").strip("]")
        return value.lower()

    @staticmethod
    def _split_schema_columns(raw: str) -> list[str]:
        values: list[str] = []
        for part in re.split(r"[,，]", str(raw or "")):
            name = part.strip().strip("-").strip()
            if not name:
                continue
            name = name.split("#", 1)[0].strip()
            if not name:
                continue
            values.append(name)
        return values

    def _extract_schema_table_columns(self, output: Any) -> dict[str, list[str]]:
        text = str(output or "")
        if not text.strip():
            return {}

        table_columns: dict[str, list[str]] = {}
        current_table = ""
        columns_mode = False
        columns_indent = 0

        def set_table(name: str) -> None:
            nonlocal current_table, columns_mode
            table = str(name or "").strip().strip('"').strip("'")
            if not table:
                return
            current_table = table
            table_columns.setdefault(self._normalize_sql_identifier(table), [])
            columns_mode = False

        def add_column(name: str) -> None:
            if not current_table:
                return
            column = str(name or "").strip().strip('"').strip("'")
            if not column:
                return
            key = self._normalize_sql_identifier(current_table)
            normalized = self._normalize_sql_identifier(column)
            existing = {self._normalize_sql_identifier(item) for item in table_columns.setdefault(key, [])}
            if normalized and normalized not in existing:
                table_columns[key].append(column)

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            header_match = re.match(r"---\s*\[Schema:\d+\].*?\btable=([^\s]+)", stripped, flags=re.IGNORECASE)
            if header_match:
                set_table(header_match.group(1))
                continue

            indent = len(line) - len(line.lstrip())
            if columns_mode and indent <= columns_indent and not stripped.startswith("-"):
                columns_mode = False

            table_match = re.match(r"table_name:\s*([A-Za-z_][\w.$]*)\s*$", stripped, flags=re.IGNORECASE)
            if table_match:
                set_table(table_match.group(1))
                continue

            inline_columns = re.match(r"columns:\s*(.+)$", stripped, flags=re.IGNORECASE)
            if inline_columns:
                columns_mode = True
                columns_indent = indent
                for column in self._split_schema_columns(inline_columns.group(1)):
                    add_column(column)
                continue

            if re.match(r"columns:\s*$", stripped, flags=re.IGNORECASE):
                columns_mode = True
                columns_indent = indent
                continue

            physical_match = re.match(r"-\s*physical_name:\s*([A-Za-z_][\w.$]*)\s*$", stripped, flags=re.IGNORECASE)
            name_match = re.match(r"-\s*name:\s*([A-Za-z_][\w.$]*)\s*$", stripped, flags=re.IGNORECASE)
            simple_column_match = re.match(r"-\s*([A-Za-z_][\w.$]*)\s*$", stripped)
            if columns_mode:
                match = physical_match or name_match or simple_column_match
                if match:
                    add_column(match.group(1))
                continue

            if physical_match:
                set_table(physical_match.group(1))

        return {
            table: columns
            for table, columns in table_columns.items()
            if columns
        }

    def _build_schema_binding_summary(self, output: Any) -> str:
        table_columns = self._extract_schema_table_columns(output)
        if not table_columns:
            return ""
        lines = [
            "【Schema Binding 摘要】",
            "以下为本轮 SQL 允许优先绑定的物理表与字段，请先完成“业务含义 -> 物理字段”的映射再生成 SQL。",
        ]
        for table, columns in list(table_columns.items())[:8]:
            visible_columns = ", ".join(columns[:60])
            if len(columns) > 60:
                visible_columns += f", ... 共 {len(columns)} 列"
            lines.append(f"- {table.upper()}: {visible_columns}")
        lines.append("约束：禁止使用未列出的字段；若用户口径无法绑定到上述字段，请先重查 Schema 或澄清，不要臆造英文列名。")
        return "\n".join(lines)

    @staticmethod
    def _is_date_format_sql_error(message: str) -> bool:
        text = str(message or "").lower()
        if not text.strip():
            return False
        patterns = (
            "ora-01861",
            "ora-01830",
            "literal does not match format string",
            "date format",
            "datetime format",
            "cannot parse datetime",
            "cannot parse date",
        )
        return any(pattern in text for pattern in patterns)

    @staticmethod
    def _mask_sql_literals_and_comments(sql: str) -> str:
        text = str(sql or "")
        chars = list(text)
        i = 0
        while i < len(chars):
            ch = chars[i]
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if ch == "-" and nxt == "-":
                chars[i] = chars[i + 1] = " "
                i += 2
                while i < len(chars) and chars[i] not in "\r\n":
                    chars[i] = " "
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                i += 2
                while i < len(chars):
                    if chars[i] == "*" and i + 1 < len(chars) and chars[i + 1] == "/":
                        chars[i] = chars[i + 1] = " "
                        i += 2
                        break
                    chars[i] = " "
                    i += 1
                continue
            if ch == "'":
                chars[i] = " "
                i += 1
                while i < len(chars):
                    chars[i] = " "
                    if text[i] == "'" and i + 1 < len(chars) and text[i + 1] == "'":
                        chars[i + 1] = " "
                        i += 2
                        continue
                    if text[i] == "'":
                        i += 1
                        break
                    i += 1
                continue
            i += 1
        return "".join(chars)

    def _build_sql_schema_preflight_error(
        self,
        sql: str,
        schema_table_columns: dict[str, list[str]],
    ) -> str:
        if not sql or not schema_table_columns:
            return ""

        sql_for_preflight = self._mask_sql_literals_and_comments(sql)
        alias_to_table: dict[str, str] = {}
        table_displays: dict[str, str] = {}
        table_pattern = re.compile(
            r"\b(?:from|join)\s+([A-Za-z_][\w.$]*)(?:\s+(?:as\s+)?([A-Za-z_][\w$]*))?",
            flags=re.IGNORECASE,
        )
        reserved_aliases = {
            "where", "join", "left", "right", "inner", "outer", "full", "cross", "on",
            "group", "order", "having", "limit", "fetch", "union", "where",
        }
        for match in table_pattern.finditer(sql_for_preflight):
            table = match.group(1).split(".")[-1]
            alias = match.group(2) or table
            alias_norm = self._normalize_sql_identifier(alias)
            table_norm = self._normalize_sql_identifier(table)
            if alias_norm in reserved_aliases:
                alias_norm = table_norm
            if table_norm not in schema_table_columns:
                continue
            alias_to_table[alias_norm] = table_norm
            alias_to_table[table_norm] = table_norm
            table_displays[table_norm] = table

        if not alias_to_table:
            return ""

        for qualifier, column in re.findall(r"\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\b", sql_for_preflight):
            qualifier_norm = self._normalize_sql_identifier(qualifier)
            table_norm = alias_to_table.get(qualifier_norm)
            if not table_norm:
                continue
            allowed_columns = schema_table_columns.get(table_norm) or []
            allowed_norms = {self._normalize_sql_identifier(item) for item in allowed_columns}
            column_norm = self._normalize_sql_identifier(column)
            if column_norm not in allowed_norms:
                table_display = table_displays.get(table_norm) or table_norm
                available = ", ".join(allowed_columns[:40])
                return (
                    "[TOOL_ERROR] SQL 预检失败：字段/表引用错误。"
                    f'ORA-00904: "{qualifier.upper()}"."{column.upper()}": invalid identifier。'
                    f"字段 {qualifier}.{column} 不在 get_dataset_schema 返回的表 {table_display} 字段列表中。"
                    f"可用字段：{available}。请替换或删除该字段后再调用 execute_sql_query。"
                )
        return ""

    @staticmethod
    def _normalize_tool_arg_value(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): DataAgentRunner._normalize_tool_arg_value(value[key])
                for key in sorted(value.keys(), key=str)
            }
        if isinstance(value, list):
            return [DataAgentRunner._normalize_tool_arg_value(item) for item in value]
        if isinstance(value, str):
            return " ".join(value.strip().split())
        return value

    @classmethod
    def _tool_call_signature(cls, tool_name: str, tool_args: dict[str, Any] | None) -> str:
        normalized_args = cls._normalize_tool_arg_value(tool_args or {})
        try:
            args_text = json.dumps(normalized_args, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            args_text = str(normalized_args)
        return f"{tool_name}:{args_text}"

    def _record_tool_call_signature(
        self,
        state: _DataRunState,
        tool_name: str,
        tool_args: dict[str, Any] | None,
    ) -> None:
        if not tool_name or state.tool_loop_fuse_triggered:
            return
        signature = self._tool_call_signature(tool_name, tool_args)
        if self._tool_call_made_progress(state, tool_name):
            state.tool_call_signatures.pop(signature, None)
            state.tool_loop_detector = ToolLoopDetector()
            return
        fuse_threshold = TOOL_LOOP_FUSE_THRESHOLD
        if tool_name == "execute_sql_query" and (
            state.last_sql_error_summary or state.failed_sql_signatures
        ):
            fuse_threshold = FAILED_SQL_REPEAT_THRESHOLD
        verdict = state.tool_loop_detector.record(tool_name, tool_args)
        count = verdict.count
        if verdict.fused:
            state.tool_loop_fuse_triggered = True
            state.halt_current_react = True
            state.tool_loop_fuse_reason = verdict.message
            return
        if count >= fuse_threshold:
            state.tool_loop_fuse_triggered = True
            state.halt_current_react = True
            suffix = (
                "，且近期存在 SQL 执行失败，系统判断继续原样重试只会消耗步数。"
                if fuse_threshold < TOOL_LOOP_FUSE_THRESHOLD
                else "，系统判断继续执行大概率只会消耗步数。"
            )
            state.tool_loop_fuse_reason = (
                f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次{suffix}"
            )
            return
        count = state.tool_call_signatures.get(signature, 0) + 1
        state.tool_call_signatures[signature] = count
        if count >= fuse_threshold:
            state.tool_loop_fuse_triggered = True
            state.halt_current_react = True
            suffix = (
                "，且近期存在 SQL 执行失败，系统判断继续原样重试只会消耗步数。"
                if fuse_threshold < TOOL_LOOP_FUSE_THRESHOLD
                else "，系统判断继续执行大概率只会消耗步数。"
            )
            state.tool_loop_fuse_reason = (
                f"工具 `{tool_name}` 使用相同参数连续/重复调用 {count} 次{suffix}"
            )

    @staticmethod
    def _schema_keywords_from_args(tool_args: dict[str, Any] | None) -> str:
        if not isinstance(tool_args, dict):
            return ""
        raw = tool_args.get("keywords") or tool_args.get("query") or tool_args.get("input")
        if isinstance(raw, list):
            return " ".join(str(item).strip() for item in raw if str(item).strip())
        return str(raw or "").strip()

    def _record_schema_keywords(
        self,
        state: _DataRunState,
        tool_args: dict[str, Any] | None,
    ) -> None:
        keywords = str(state.last_applied_schema_retry_keywords or "").strip()
        if not keywords:
            keywords = self._schema_keywords_from_args(tool_args)
        if keywords:
            state.last_schema_keywords = keywords

    @staticmethod
    def _append_unique_keyword(tokens: list[str], keyword: str) -> None:
        normalized = " ".join(str(keyword or "").strip().split())
        if normalized and normalized not in tokens:
            tokens.append(normalized)

    @staticmethod
    def _clean_schema_retry_phrase(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"\d{4}[-/年]\d{1,2}[-/月]?\d{0,2}日?", " ", cleaned)
        cleaned = re.sub(r"\d{1,2}月\d{0,2}日?", " ", cleaned)
        for word in SCHEMA_RETRY_STOPWORDS:
            cleaned = cleaned.replace(word, " ")
        cleaned = re.sub(r"[?？!！。；;：:，,、\[\]（）(){}<>《》\"'`]+", " ", cleaned)
        return " ".join(cleaned.split())

    def _schema_retry_core_terms(self, *sources: Any) -> list[str]:
        terms: list[str] = []
        preserved_phrases: list[str] = []
        requested_suffixes: list[str] = []
        for source in sources:
            source_text = str(source or "").strip()
            if not source_text:
                continue
            for phrase in re.split(r"[\s,，、;；]+", source_text):
                phrase = phrase.strip()
                if not phrase:
                    continue
                if phrase in SCHEMA_RETRY_SUFFIXES:
                    self._append_unique_keyword(requested_suffixes, phrase)
                    continue
                compact_metric = False
                for suffix in SCHEMA_RETRY_SUFFIXES:
                    if phrase.endswith(suffix) and len(phrase) > len(suffix):
                        stem = phrase[: -len(suffix)].strip()
                        if re.search(r"[A-Za-z]", stem):
                            compact_metric = True
                            self._append_unique_keyword(preserved_phrases, phrase)
                        else:
                            self._append_unique_keyword(requested_suffixes, suffix)
                        break
                if compact_metric:
                    continue
                cleaned = self._clean_schema_retry_phrase(phrase)
                if cleaned:
                    for term in cleaned.split():
                        self._append_unique_keyword(terms, term)
                    if (
                        cleaned != phrase
                        and len(phrase) <= 16
                        and phrase.startswith(("所有", "全部"))
                    ):
                        self._append_unique_keyword(preserved_phrases, phrase)
        return [
            *terms,
            *[f"__phrase__{phrase}" for phrase in preserved_phrases],
            *[f"__suffix__{suffix}" for suffix in requested_suffixes],
        ]

    def _build_controlled_schema_retry_keywords(self, *sources: Any) -> str:
        terms_and_markers = self._schema_retry_core_terms(*sources)
        if not terms_and_markers:
            return ""
        requested_suffixes = [
            value.removeprefix("__suffix__")
            for value in terms_and_markers
            if value.startswith("__suffix__")
        ]
        preserved_phrases = [
            value.removeprefix("__phrase__")
            for value in terms_and_markers
            if value.startswith("__phrase__")
        ]
        core_terms = [
            value
            for value in terms_and_markers
            if not value.startswith("__suffix__") and not value.startswith("__phrase__")
        ]
        if not core_terms and not preserved_phrases:
            return ""

        tokens: list[str] = []
        for term in core_terms:
            self._append_unique_keyword(tokens, term)
        for phrase in preserved_phrases:
            self._append_unique_keyword(tokens, phrase)
        base_terms = [
            term
            for term in core_terms
            if len(term) <= 12 and not any(term.endswith(suffix) for suffix in SCHEMA_RETRY_SUFFIXES)
        ]
        if len(base_terms) >= 2 and not requested_suffixes:
            self._append_unique_keyword(tokens, "".join(base_terms[:2]))
        for term in base_terms[:4]:
            for suffix in requested_suffixes:
                self._append_unique_keyword(tokens, f"{term}{suffix}")
        return " ".join(tokens[:32])

    def _prepare_controlled_schema_retry_keywords(
        self,
        state: _DataRunState,
        user_question: str = "",
    ) -> None:
        retry_keywords = self._build_controlled_schema_retry_keywords(
            state.last_schema_keywords,
            self._schema_search_keywords,
            self._standalone_query,
            user_question,
        )
        state.controlled_schema_retry_keywords = retry_keywords
        state.pending_schema_retry = bool(retry_keywords.strip())

    def _tool_call_made_progress(self, state: _DataRunState, tool_name: str) -> bool:
        if tool_name == "get_dataset_schema":
            return (
                state.schema_completed
                and not self._is_schema_fatal(state)
                and not state.schema_miss
                and not state.schema_needs_refinement
                and not state.schema_ambiguous
            )
        if tool_name == "execute_sql_query":
            return (
                state.sql_completed
                and not state.sql_error
                and not state.empty_sql_result
                and not state.sql_plan_missing
                and not state.sql_static_risk
                and not state.sql_repeat_gate_block
                and not state.failed_sql_repeat_gate_block
                and not state.ratio_anomaly
                and not state.duration_anomaly
                and not state.diagnostic_sql_pending_final
            )
        return False

    def _wrap_tools_with_schema_gate(
        self,
        tools: list[RuntimeToolSpec],
        state: _DataRunState,
    ) -> list[RuntimeToolSpec]:
        if not state.requires_fresh_data:
            return tools

        wrapped: list[RuntimeToolSpec] = []
        for spec in tools:
            if spec.name == "get_dataset_schema":
                original_callable = spec.callable

                async def invoke_schema_controlled(*, _original=original_callable, **kwargs: Any) -> Any:
                    controlled_keywords = str(state.controlled_schema_retry_keywords or "").strip()
                    use_controlled = bool(
                        controlled_keywords
                        and (state.pending_schema_retry or state.schema_miss)
                    )
                    if use_controlled:
                        kwargs["keywords"] = controlled_keywords
                        state.last_applied_schema_retry_keywords = controlled_keywords
                        state.pending_schema_retry = False
                    else:
                        state.last_applied_schema_retry_keywords = ""
                    applied_kw = kwargs.get("keywords") or kwargs.get("query")
                    state.last_schema_tool_keywords = str(applied_kw or "").strip()
                    result = _original(**kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    return result

                wrapped.append(replace(spec, callable=invoke_schema_controlled))
                continue

            if spec.name != "execute_sql_query":
                wrapped.append(spec)
                continue

            original_callable = spec.callable

            async def invoke_sql_gated(*, _original=original_callable, **kwargs: Any) -> Any:
                if state.schema_ambiguous:
                    return (
                        f"{SCHEMA_GATE_PREFIX} 当前 Schema 检索返回多个高置信度候选，"
                        "需要先请用户确认具体数据集或指标口径，禁止直接执行 SQL。"
                    )
                if not state.schema_completed:
                    return (
                        f"{SCHEMA_GATE_PREFIX} 为保证数据准确性，必须先调用 get_dataset_schema "
                        "获取数据集定义，再执行 execute_sql_query。"
                    )
                if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
                    return (
                        f"{SCHEMA_GATE_PREFIX} 上一轮 SQL 因字段/表引用错误失败，必须先重新调用 "
                        "get_dataset_schema 核对物理列名、表名与 JOIN 键，再修正并执行 SQL。"
                    )

                current_sql = str(kwargs.get("sql") or kwargs.get("query") or "").strip()
                current_sql_normalized = self._normalize_sql_text(current_sql)

                if state.requires_sql_plan and not state.sql_plan_seen:
                    state.sql_plan_missing = True
                    return (
                        f"{SQL_PLAN_GATE_PREFIX} 高风险 SQL 执行前缺少结构化 SQL Plan，已阻止执行。\n"
                        f"{DataQueryPrompts.HIGH_RISK_REQUIRE_PLAN}"
                    )

                if current_sql_normalized:
                    prior_failures = state.failed_sql_signatures.get(current_sql_normalized, 0)
                    if prior_failures >= 1:
                        summary = str(state.last_sql_error_summary or "").strip()
                        summary_line = f"\n上次错误摘要：{summary[:400]}" if summary else ""
                        return (
                            f"{FAILED_SQL_REPEAT_GATE_PREFIX} 该 SQL 已在上一轮执行失败，"
                            "禁止原样重复提交。请根据错误信息修正字段名、表名、JOIN 条件、"
                            f"筛选条件或聚合逻辑后再调用 execute_sql_query。{summary_line}"
                        )

                preflight_error = self._build_sql_schema_preflight_error(
                    current_sql,
                    state.schema_table_columns,
                )
                if preflight_error:
                    return preflight_error

                static_risk = ""
                if not (state.requires_sql_plan and not state.sql_plan_seen):
                    static_risk = self._detect_sql_static_risk(current_sql)
                if static_risk:
                    state.sql_static_risk = True
                    state.sql_static_risk_reason = static_risk
                    return (
                        f"{SQL_STATIC_GATE_PREFIX} SQL 存在高风险执行特征，已阻止执行：{static_risk}\n"
                        "请收窄时间范围、补充 LIMIT、避免 SELECT *，或修正 JOIN 条件后重新调用 execute_sql_query。"
                    )

                if current_sql_normalized and current_sql_normalized in state.successful_sqls:
                    cached_output = state.successful_sqls[current_sql_normalized]
                    return (
                        f"{SQL_REPEAT_GATE_PREFIX} 本轮已成功执行过相同的 SQL 查询，禁止重复 execute_sql_query。\n"
                        "为保证正常输出，系统已自动为您加载该 SQL 上一次查询成功的缓存数据结果，请直接基于此数据进行回答，无需再次调用查数工具：\n\n"
                        f"{cached_output}"
                    )

                result = _original(**kwargs)
                if inspect.isawaitable(result):
                    result = await result

                try:
                    if (
                        result
                        and not self._is_schema_gate_block(result)
                        and not self._is_sql_repeat_gate_block(result)
                        and not self._is_sql_static_gate_block(result)
                        and not self._is_sql_plan_gate_block(result)
                    ):
                        parsed_output = self._try_parse_json_output(result)
                        empty_reason = self._detect_empty_result(parsed_output)
                        sql_error, _ = self._detect_sql_error(result)
                        duration_anomaly, _ = self._detect_duration_anomaly(parsed_output)
                        if not sql_error and not empty_reason and not duration_anomaly:
                            if current_sql_normalized:
                                state.successful_sqls[current_sql_normalized] = result
                            state.last_successful_sql_output = result
                except Exception:
                    pass

                return result

            wrapped.append(replace(spec, callable=invoke_sql_gated))
        return wrapped

    async def _build_native_agent(
        self,
        *,
        native_model: Any,
        tools: list[RuntimeToolSpec],
        system_content: str,
        max_steps: int,
        primary_model_name: str,
        restored_state: Any = None,
    ) -> Any:
        # ChatBI 挂载查数相关工具与系统隐式工具；不合并 workspace 的 Grep/Read/Bash，避免模型跳过 get_dataset_schema。
        toolkit = build_toolkit(
            tools,
            approval_mode=self.permission_options.get("approval_mode"),
        )
        workspace = await get_local_workspace(
            user_id=self._current_user_id(),
            conversation_id=self.conversation_id,
        )
        context_config = await load_context_config()
        model_config = await build_model_config(
            config=self.config,
            primary_model_name=primary_model_name,
        )
        middlewares = []
        if self.conversation_id:
            from app.services.ai.runtime.agentscope.middleware import ModelCallStatsMiddleware
            middlewares.append(
                ModelCallStatsMiddleware(
                    user_id=self._current_user_id(),
                    conversation_id=self.conversation_id,
                    agent_name=self._runtime_agent_name(),
                    trace_id=self.trace_id,
                )
            )
        kwargs: Dict[str, Any] = {
            "name": self._runtime_agent_name(),
            "system_prompt": system_content,
            "model": native_model,
            "toolkit": toolkit,
            "react_config": ReActConfig(max_iters=max_steps),
            "middlewares": middlewares,
        }
        if restored_state is not None:
            kwargs["state"] = restored_state
        if workspace is not None:
            kwargs["offloader"] = workspace
        if model_config is not None:
            kwargs["model_config"] = model_config
        if context_config is not None:
            kwargs["context_config"] = context_config
        return Agent(**kwargs)

    async def execute(
        self,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        model_name = resolve_runtime_model_name(self.config, prefer_synthesis=True)
        incompatible_msg = await ensure_multimodal_compatible(history, model_name)
        if incompatible_msg:
            yield {"content": incompatible_msg, "status": "error"}
            return

        runtime_messages = [
            message
            for message in normalize_messages_for_llm(convert_history_to_messages(history, strip_thought=True))
            if not isinstance(message, SystemMessage)
        ]
        user_question = next(
            (str(getattr(message, "content", "")) for message in reversed(runtime_messages)),
            "",
        )
        last_data_result_for_turn = await self._load_last_data_result_with_retry()
        turn_cls, turn_intent_info, turn_elapsed_ms = await resolve_data_query_turn_classification(
            user_question,
            history,
            user_info=self.user_info,
            conversation_id=self.conversation_id,
            has_last_data_result=last_data_result_for_turn is not None,
        )
        self.turn_classification = turn_cls
        self.intent_info = turn_intent_info
        self.intent_elapsed_ms = turn_elapsed_ms
        self._requires_fresh_data = turn_cls.requires_fresh_data
        self._requires_sql_query = bool(getattr(turn_cls, "requires_sql_query", True))
        yield {
            "type": "log",
            "id": f"chatbi_turn_{uuid.uuid4().hex[:8]}",
            "title": "ChatBI 请求类别分析结果",
            "details": f"{data_query_turn_type_label(turn_cls.turn_type)}。{turn_cls.reasoning}",
            "status": "success",
            "category": "intent",
            "turn_type": turn_cls.turn_type.value,
            "execution_time_ms": turn_elapsed_ms,
        }

        if turn_cls.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT:
            if not last_data_result_for_turn:
                last_data_result_for_turn = await self._load_last_data_result_with_retry()
            if last_data_result_for_turn:
                async for chunk in self._synthesize_from_last_data_result(
                    runtime_messages,
                    self.config.system_prompt or "",
                    user_question,
                    last_data_result_for_turn,
                ):
                    yield chunk
            elif history_shows_recent_data_result(history):
                async for chunk in self._synthesize_from_history_data_result(
                    runtime_messages,
                    self.config.system_prompt or "",
                    user_question,
                    history,
                ):
                    yield chunk
            else:
                async for chunk in self._yield_missing_reusable_result_clarification(
                    history,
                    user_question=user_question,
                ):
                    yield chunk
            return

        if turn_cls.turn_type == DataQueryTurnType.CLARIFICATION_OR_NON_DATA:
            async for chunk in self._yield_contextual_clarification(
                user_question=user_question,
                history=history,
                reasoning=turn_cls.reasoning,
            ):
                yield chunk
            return

        tools = await self._resolve_runtime_tools_from_config()
        max_steps = await self._resolve_max_steps()
        self._standalone_query = user_question
        if turn_cls.requires_fresh_data:
            self._standalone_query = await self._resolve_standalone_query_for_new_data_query(
                user_question,
                runtime_messages,
            )
        system_content = await self._build_system_content(
            context_action_result=last_data_result_for_turn if not turn_cls.requires_fresh_data else None,
            include_context_action=not turn_cls.requires_fresh_data,
        )
        if turn_cls.requires_few_shot:
            system_content = await self._inject_few_shot_examples(
                system_content,
                user_question=self._standalone_query,
                runtime_messages=runtime_messages,
            )
            if self._pending_few_shot_log:
                yield self._pending_few_shot_log
                self._pending_few_shot_log = None
        else:
            yield {
                "type": "log",
                "id": f"fewshot_search_{uuid.uuid4().hex[:8]}",
                "title": "跳过经验库检索",
                "details": "本轮无需新 SQL 生成，已跳过经验库检索以节省延迟。",
                "status": "success",
                "execution_time_ms": 0,
            }
        if turn_cls.requires_fresh_data and turn_cls.requires_few_shot:
            need_analysis_start = time.time()
            need_analysis_log_id = f"need_analysis_{uuid.uuid4().hex[:8]}"
            yield {
                "type": "log",
                "id": need_analysis_log_id,
                "title": "用户需求分析",
                "details": (
                    "正在结合用户原始问题与经验库案例，生成用于元数据检索的问题关键词..."
                    if self._fewshot_examples
                    else "正在分析用户原始问题，生成用于元数据检索的问题关键词..."
                ),
                "status": "pending",
                "started_at": int(need_analysis_start * 1000),
            }
            self._schema_search_keywords = await self._plan_schema_search_keywords(
                user_question,
                self._standalone_query,
                self._fewshot_examples,
            )
            system_content += (
                "\n\n【Schema 检索词规划】本轮已结合"
                + ("用户原始问题和经验库案例" if self._fewshot_examples else "用户原始问题")
                + f"规划出 get_dataset_schema 的检索词：{self._schema_search_keywords}\n"
                "首次检索数据集定义时，请优先使用这些 keywords；这些词仅用于检索元数据，不代表最终 SQL 表字段已确认。"
                f"\n【独立查数问题】{self._standalone_query}"
            )
            yield {
                "type": "log",
                "id": need_analysis_log_id,
                "title": "用户需求分析",
                "details": (
                    "已完成用户需求分析，并生成问题关键词。"
                    f"\n问题关键词: {self._schema_search_keywords or self._standalone_query or user_question}"
                ),
                "status": "success",
                "execution_time_ms": (time.time() - need_analysis_start) * 1000,
            }

        prefetched_schema_output: str | None = None
        if turn_cls.requires_fresh_data:
            await self._ensure_schema_similarity_threshold()
            schema_keywords = (
                self._schema_search_keywords
                or self._standalone_query
                or user_question
                or ""
            ).strip()
            async for chunk in self._auto_invoke_get_dataset_schema(
                keywords=schema_keywords,
                tools=tools,
            ):
                if chunk.get("__schema_output__") is not None:
                    prefetched_schema_output = str(chunk["__schema_output__"])
                    continue
                yield chunk
            if prefetched_schema_output:
                prefetch_state = _DataRunState()
                self._apply_schema_tool_result(prefetch_state, prefetched_schema_output)
                if self._is_schema_fatal(prefetch_state):
                    async for chunk in self._yield_schema_fatal_abort(
                        prefetch_state,
                        prefetched_schema_output,
                    ):
                        yield chunk
                    return
                system_content += (
                    "\n\n"
                    + DataQueryPrompts.prefetched_schema_context(
                        schema_keywords,
                        prefetched_schema_output,
                    )
                )
                schema_binding_summary = self._build_schema_binding_summary(prefetched_schema_output)
                if schema_binding_summary:
                    system_content += f"\n\n{schema_binding_summary}"

        llm_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        native_model = getattr(llm_handle, "native_model", None)
        if native_model is None:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前模型适配器未提供 AgentScope native_model，无法执行 ChatBI AgentScope 原生工具链。",
            }
            return

        agent_name = self._runtime_agent_name()
        tools_fingerprint = build_tools_fingerprint(self.config, tools)
        model_name = getattr(native_model, "model", self.config.model_name)
        try:
            async with agentscope_session_lock.hold(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
                ttl_seconds=300,
            ):
                async for chunk in self._run_native_agent_turn(
                    native_model=native_model,
                    tools=tools,
                    tools_fingerprint=tools_fingerprint,
                    model_name=model_name,
                    agent_name=agent_name,
                    system_content=system_content,
                    max_steps=max_steps,
                    llm_handle=llm_handle,
                    runtime_messages=runtime_messages,
                    user_question=user_question,
                    turn_cls=turn_cls,
                    prefetched_schema_output=prefetched_schema_output,
                ):
                    yield chunk
        except SessionLockTimeout:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }

    async def _run_native_agent_turn(
        self,
        *,
        native_model: Any,
        tools: list[RuntimeToolSpec],
        tools_fingerprint: str,
        model_name: Any,
        agent_name: str,
        system_content: str,
        max_steps: int,
        llm_handle: Any,
        runtime_messages: List[Any],
        user_question: str,
        turn_cls: Any,
        prefetched_schema_output: str | None = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        await self._ensure_schema_similarity_threshold()
        restored_state = None
        # 优化：新数据查询 (requires_fresh_data=True) 路径下，不从 Redis 恢复旧的 AgentState 内存，避免历史 DDL 等累积导致首个 Prompt Token 爆炸
        if not turn_cls.requires_fresh_data:
            persisted = await agent_state_store.load(
                self._runtime_user_id(),
                self.conversation_id,
                agent_name,
            )
            if persisted:
                if persisted.matches(
                    tools_fingerprint=tools_fingerprint,
                    agent_name=agent_name,
                ):
                    try:
                        from agentscope.state import AgentState

                        restored_state = AgentState.model_validate(persisted.state)
                    except Exception as exc:
                        logger.warning("[DataAgentRunner] Failed to restore AgentState: %s", exc)
                else:
                    logger.warning(
                        "[DataAgentRunner] Tools fingerprint mismatch for agent=%s (stored=%s, current=%s). "
                        "Resetting conversation state to prevent tool call conflicts.",
                        agent_name, persisted.tools_fingerprint, tools_fingerprint
                    )
                    yield {
                        "type": "log",
                        "id": f"state_reset_{uuid.uuid4().hex[:8]}",
                        "title": "智能体配置变更：历史会话状态已重置",
                        "details": "检测到绑定的工具集或模型配置发生改变，为防工具调用崩溃，已重置运行时状态。",
                        "status": "warning",
                    }

        state = _DataRunState()
        state.requires_fresh_data = turn_cls.requires_fresh_data
        state.requires_sql_query = bool(getattr(turn_cls, "requires_sql_query", True))
        state.requires_sql_plan = (
            self._is_sql_plan_enabled()
            and state.requires_fresh_data
            and state.requires_sql_query
            and self._should_require_sql_plan(user_question)
        )
        if prefetched_schema_output is not None:
            state.last_schema_keywords = (
                self._schema_search_keywords
                or self._standalone_query
                or user_question
                or ""
            ).strip()
            self._apply_schema_tool_result(state, prefetched_schema_output)
            if state.schema_miss:
                self._prepare_controlled_schema_retry_keywords(state, user_question)
            if self._is_schema_fatal(state):
                async for chunk in self._yield_schema_fatal_abort(state, prefetched_schema_output):
                    yield chunk
                return
        guarded_tools = self._wrap_tools_with_schema_gate(tools, state)

        agent = await self._build_native_agent(
            native_model=native_model,
            tools=guarded_tools,
            system_content=system_content,
            max_steps=max_steps,
            restored_state=restored_state,
            primary_model_name=str(getattr(llm_handle, "model_name", self.config.model_name) or ""),
        )
        if self._standalone_query and self._standalone_query != user_question:
            # 仅保留最近 3 轮对话历史（6 条 Human/AI 消息），工具消息不计入轮数
            conv_only = [m for m in runtime_messages[:-1] if isinstance(m, (HumanMessage, AIMessage))]
            last_history = conv_only[-6:]
            runtime_messages = [
                *last_history,
                HumanMessage(content=self._standalone_query),
            ]
        if restored_state and restored_state.context:
            inputs = to_agentscope_messages(
                compat_to_runtime_messages(self._latest_user_runtime_messages(runtime_messages))
            )
        else:
            inputs = to_agentscope_messages(compat_to_runtime_messages(runtime_messages))
        stream_meta = {
            "system_content": system_content,
            "max_steps": max_steps,
            "user_question": user_question,
        }
        interrupted = False
        initial_tool_choice = self._resolve_initial_tool_choice(state)
        original_model = agent.model
        
        # 优化：如果在 prefetch 阶段就已经确定 schema 检索未命中 (schema_miss)，直接短路跳过首轮 LLM 交互以节省延迟与 Token，下沉进后面的修复轮次
        if state.schema_miss:
            logger.info("[DataAgentRunner] Prefetch schema miss, skipping initial LLM interaction and entering repair directly.")
        else:
            if initial_tool_choice is not None:
                agent.model = _ForcedFirstToolChoiceModel(original_model, initial_tool_choice)
            try:
                async for chunk in self._stream_agentscope_events(
                    event_stream=agent.reply_stream(inputs),
                    agent=agent,
                    tools=guarded_tools,
                    native_model=native_model,
                    state=state,
                    stream_meta=stream_meta,
                    emit_final_guard=False,
                ):
                    if is_interrupt_sse_chunk(chunk):
                        interrupted = True
                    yield chunk
            finally:
                agent.model = original_model
        if interrupted:
            return
        if self._is_schema_fatal(state):
            async for chunk in self._yield_schema_fatal_abort(state):
                yield chunk
            return
        if state.full_content and self._current_repair_kind(state):
            async for chunk in self._retract_provisional_content_before_repair(
                state,
                reason="main-loop content followed by a repair condition",
            ):
                yield chunk
        if state.full_content:
            deduped = finalize_visible_reply(state.full_content)
            if deduped != state.full_content:
                logger.warning(
                    "[DataAgentRunner] Collapsed duplicated main-loop content (%d -> %d chars)",
                    len(state.full_content), len(deduped),
                )
                state.full_content = deduped
                yield {"type": "retraction", "content": deduped}
            if self.conversation_id:
                await agent_state_store.save(
                    user_id=self._runtime_user_id(),
                    conversation_id=self.conversation_id,
                    agent_name=agent_name,
                    agent_version=self.config.agent_version,
                    tools_fingerprint=tools_fingerprint,
                    model_name=str(model_name) if model_name else None,
                    state=agent.state,
                )
            return
        if state.sql_repeat_gate_block and state.last_successful_sql_output is not None:
            async for chunk in self._synthesize_from_cached_sql_result(
                runtime_messages=runtime_messages,
                system_prompt=system_content,
                user_question=user_question,
                state=state,
            ):
                yield chunk
            if self.conversation_id:
                await agent_state_store.save(
                    user_id=self._runtime_user_id(),
                    conversation_id=self.conversation_id,
                    agent_name=agent_name,
                    agent_version=self.config.agent_version,
                    tools_fingerprint=tools_fingerprint,
                    model_name=str(model_name) if model_name else None,
                    state=agent.state,
                )
            return

        max_repair_rounds = max(sum(DATA_REPAIR_BUDGETS.values()), MAX_DATA_REPAIR_ROUNDS)
        for _ in range(max_repair_rounds):
            if state.sql_fatal_error:
                break
            if self._repair_budget_exhausted(state):
                break
            repair_message = self._build_repair_message(state)
            if not repair_message:
                break
            repair_tool_choice = self._resolve_repair_tool_choice(state)
            yield {
                "type": "log",
                "id": f"data_repair_{uuid.uuid4().hex[:8]}",
                "title": self._build_repair_title(state),
                "details": repair_message,
                "status": "warning",
            }
            self._record_repair_attempt(state)
            self._reset_state_for_repair(state)
            repair_inputs = to_agentscope_messages(compat_to_runtime_messages(repair_message))
            original_model = agent.model
            if repair_tool_choice is not None:
                agent.model = _ForcedFirstToolChoiceModel(original_model, repair_tool_choice)
            try:
                async for chunk in self._stream_agentscope_events(
                    event_stream=agent.reply_stream(repair_inputs),
                    agent=agent,
                    tools=guarded_tools,
                    native_model=native_model,
                    state=state,
                    stream_meta=stream_meta,
                    emit_final_guard=False,
                ):
                    if is_interrupt_sse_chunk(chunk):
                        interrupted = True
                    yield chunk
            finally:
                agent.model = original_model
            if interrupted:
                return
            if self._is_schema_fatal(state):
                async for chunk in self._yield_schema_fatal_abort(state):
                    yield chunk
                return
            if state.sql_fatal_error:
                async for chunk in self._yield_sql_fatal_abort(state):
                    yield chunk
                return
            if state.full_content and self._current_repair_kind(state):
                async for chunk in self._retract_provisional_content_before_repair(
                    state,
                    reason="repair-loop content followed by a repair condition",
                ):
                    yield chunk
            if state.full_content:
                deduped = finalize_visible_reply(state.full_content)
                if deduped != state.full_content:
                    logger.warning(
                        "[DataAgentRunner] Collapsed duplicated repair-loop content (%d -> %d chars)",
                        len(state.full_content), len(deduped),
                    )
                    state.full_content = deduped
                    yield {"type": "retraction", "content": deduped}
                if self.conversation_id:
                    await agent_state_store.save(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        agent_name=agent_name,
                        agent_version=self.config.agent_version,
                        tools_fingerprint=tools_fingerprint,
                        model_name=str(model_name) if model_name else None,
                        state=agent.state,
                    )
                return
            if state.sql_repeat_gate_block and state.last_successful_sql_output is not None:
                async for chunk in self._synthesize_from_cached_sql_result(
                    runtime_messages=runtime_messages,
                    system_prompt=system_content,
                    user_question=user_question,
                    state=state,
                ):
                    yield chunk
                if self.conversation_id:
                    await agent_state_store.save(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        agent_name=agent_name,
                        agent_version=self.config.agent_version,
                        tools_fingerprint=tools_fingerprint,
                        model_name=str(model_name) if model_name else None,
                        state=agent.state,
                    )
                return

        if state.sql_fatal_error:
            async for chunk in self._yield_sql_fatal_abort(state):
                yield chunk
            if self.conversation_id and not interrupted:
                await agent_state_store.save(
                    user_id=self._runtime_user_id(),
                    conversation_id=self.conversation_id,
                    agent_name=agent_name,
                    agent_version=self.config.agent_version,
                    tools_fingerprint=tools_fingerprint,
                    model_name=str(model_name) if model_name else None,
                    state=agent.state,
                )
            return

        async for chunk in self._emit_final_guard(state):
            yield chunk
        if self.conversation_id and not interrupted:
            await agent_state_store.save(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
                agent_version=self.config.agent_version,
                tools_fingerprint=tools_fingerprint,
                model_name=str(model_name) if model_name else None,
                state=agent.state,
            )

    async def _inject_few_shot_examples(
        self,
        system_content: str,
        *,
        user_question: str,
        runtime_messages: List[Any],
    ) -> str:
        search_start = time.time()
        try:
            from app.services.chatbi_example_service import ExampleService

            examples = await ExampleService.search_examples(
                user_question,
                dataset_id=None,
                top_k=None,
                history=runtime_messages,
            )
            self._fewshot_examples = examples or []
            if not examples:
                elapsed_ms = (time.time() - search_start) * 1000
                self.trace_buffer.append(
                    AgentExecutionStep(
                        step_number=self._increment_step(),
                        event_type="few_shot",
                        agent_name=self.config.agent_name,
                        model=str(self.config.model_name),
                        temperature=float(self.config.temperature or 0),
                        tool_output={"examples": []},
                        raw_log=f"未命中经验库案例，检索问题：{user_question}",
                        execution_time_ms=elapsed_ms,
                        timestamp=datetime.now(),
                    )
                )
                self._pending_few_shot_log = {
                    "type": "log",
                    "id": f"fewshot_{uuid.uuid4().hex[:6]}",
                    "title": "未命中经验库案例",
                    "details": (
                        "已完成经验库检索，但未找到足够相似的历史优质 SQL 案例。\n"
                        "本轮将继续基于用户问题和数据集定义生成 SQL。"
                    ),
                    "status": "success",
                    "execution_time_ms": elapsed_ms,
                }
                return system_content

            max_sim = max([ex.get("similarity", 0) for ex in examples])
            sim_status = "匹配度极高" if max_sim >= 0.80 else "匹配度一般"
            hit_titles = [
                f"#{ex.get('id', '?')} 「{str(ex.get('question', ''))[:15]}...」 (相似度: {ex.get('similarity', 0):.2f})"
                for ex in examples
            ]
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self._increment_step(),
                    event_type="few_shot",
                    agent_name=self.config.agent_name,
                    model=str(self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_output={"examples": examples},
                    raw_log="\n".join(hit_titles),
                    execution_time_ms=0,
                    timestamp=datetime.now(),
                )
            )
            few_shot_block = ExampleService.build_few_shot_prompt(examples)
            if few_shot_block:
                system_content = f"{few_shot_block}\n\n---\n\n{system_content}"

            example_ids = [ex["id"] for ex in examples if ex.get("id")]
            similarities = [ex.get("similarity", 0) for ex in examples if ex.get("id")]
            if example_ids:
                try:
                    await ExampleService.record_usage(example_ids, self.trace_id, similarities=similarities)
                except Exception as ex_rec:
                    logger.warning("[DataAgentRunner] Failed to record few-shot example usage stats: %s", ex_rec)

            self._pending_few_shot_log = {
                "type": "log",
                "id": f"fewshot_{uuid.uuid4().hex[:6]}",
                "title": f"✨ 命中经验库案例 ({len(examples)}条, {sim_status})",
                "details": (
                    "已匹配到历史优质 SQL 案例：\n"
                    + "\n".join(hit_titles)
                    + f"\n\n当前最高相似度: {max_sim:.2f}。这些案例将作为强制性参考引导模型生成 SQL，以减少冗余迭代。"
                ),
                "status": "success",
                "execution_time_ms": (time.time() - search_start) * 1000,
            }
            return system_content
        except Exception as e:
            self._fewshot_examples = []
            logger.warning("[DataAgentRunner] Failed to search/inject few-shot examples: %s", e)
            elapsed_ms = (time.time() - search_start) * 1000
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self._increment_step(),
                    event_type="few_shot",
                    agent_name=self.config.agent_name,
                    model=str(self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_output={"examples": []},
                    raw_log=f"经验库检索不可用，已跳过案例注入：{e}",
                    execution_time_ms=elapsed_ms,
                    status="success",
                    timestamp=datetime.now(),
                )
            )
            self._pending_few_shot_log = {
                "type": "log",
                "id": f"fewshot_{uuid.uuid4().hex[:6]}",
                "title": "经验库检索不可用",
                "details": (
                    "经验库检索本轮未完成，已自动跳过案例注入。\n"
                    "本轮将继续基于用户问题和数据集定义生成 SQL。"
                ),
                "status": "success",
                "execution_time_ms": elapsed_ms,
            }
            return system_content

    @staticmethod
    def _example_schema_keyword_context(examples: List[Dict[str, Any]], limit: int = 3) -> str:
        if not examples:
            return "无"

        blocks = []
        for idx, ex in enumerate(examples[:limit], 1):
            sql = str(ex.get("sql") or "")
            sql_meta = ex.get("sql_metadata") if isinstance(ex.get("sql_metadata"), dict) else {}
            tables = list(sql_meta.get("tables") or [])
            dimensions = list(sql_meta.get("dimensions") or [])
            if not tables and sql:
                table_matches = re.findall(r"\b(?:FROM|JOIN)\s+([`\w.]+)", sql, flags=re.IGNORECASE)
                tables = [table.strip("`") for table in table_matches]
            column_like_tokens = []
            if sql:
                for token in re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", sql):
                    if token.upper() in {
                        "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
                        "GROUP", "ORDER", "LIMIT", "SUM", "COUNT", "AVG", "MAX", "MIN", "AND", "OR",
                        "ON", "BY", "AS", "DESC", "ASC", "WITH", "CASE", "WHEN", "THEN", "ELSE", "END",
                    }:
                        continue
                    column_like_tokens.append(token)
            deduped_tokens = list(dict.fromkeys(column_like_tokens))[:20]
            blocks.append(
                "\n".join(
                    [
                        f"案例 {idx}:",
                        f"- 历史问题: {ex.get('question') or ''}",
                        f"- 数据集: {ex.get('dataset_name') or ''}",
                        f"- 核心表: {', '.join(tables[:8]) if tables else ''}",
                        f"- 核心维度: {', '.join(dimensions[:8]) if dimensions else ''}",
                        f"- SQL 关键词: {', '.join(deduped_tokens)}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _clean_schema_fallback_query(text: str) -> str:
        stop_words = ["分析", "统计", "查询", "获取", "列出", "展示", "显示", "查一下", "情况", "关于", "的", "在", "内", "后"]
        cleaned = text
        for word in stop_words:
            cleaned = cleaned.replace(word, " ")
        return " ".join(cleaned.split())

    async def _plan_schema_search_keywords(
        self,
        user_question: str,
        standalone_query: str,
        examples: List[Dict[str, Any]],
    ) -> str:
        fallback_query = self._clean_schema_fallback_query((standalone_query or user_question or "").strip())[:300]
        if len(fallback_query) < 12 and re.match(r"^[\u4e00-\u9fa5\w\s-]+$", fallback_query):
            query_lower = fallback_query.lower()
            sql_keywords = {"select", "show", "list", "查询", "列出", "展示", "显示", "获取", "查一下", "统计"}
            if not any(kw in query_lower for kw in sql_keywords):
                logger.info(
                    "[DataAgentRunner] Schema keyword planner bypassed for query: %s",
                    fallback_query
                )
                return fallback_query
        prompt = (
            "你是 ChatBI 的元数据检索词规划器。你的任务不是生成 SQL，而是为 get_dataset_schema(keywords) "
            "生成最适合检索数据集/表/字段/指标定义的短关键词。\n\n"
            "要求：\n"
            "1. 结合用户原始问题、独立查数问题 and 命中的历史案例；若没有历史案例，也必须从用户需求中抽取关键词。\n"
            "2. 优先保留业务对象/实体、指标、维度、时间字段含义，以及历史案例中出现过的物理表名/字段名。\n"
            "3. 去掉无助于元数据检索的动作词、礼貌词、排序数量描述和 SQL 生成意图。\n"
            "4. 不要生成 SQL，不要编造案例中没有出现的新物理表名。\n"
            "5. keywords 必须是空格分隔的关键词短语，优先 3 到 10 个词；不要输出完整查询句子。\n"
            "6. 严禁直接输出用户原始的长句子或问题原句，必须将其拆解为多个空格分隔的核心业务名词。\n"
            "   - 反例：如果输入为“分析注册用户在注册后 7 天内的活跃情况”，你绝对不能返回 `{\"keywords\": \"分析注册用户在注册后 7 天内的活跃情况\"}`。\n"
            "   - 正例：高品质的输出应为 `{\"keywords\": \"注册用户 活跃\"}`。\n"
            "7. 只返回 JSON。示例：{\"keywords\":\"商品 销售额 省份 product_order_detail product_name sales_amount\"}。\n"
            "8. keywords 不能为空，禁止输出 `...`、`关键词`、`N/A` 这类占位符。\n\n"
            f"【用户原始问题】\n{user_question}\n\n"
            f"【独立查数问题】\n{standalone_query}\n\n"
            f"【命中的历史案例线索】\n{self._example_schema_keyword_context(examples)}"
        )
        try:
            model = await AgentConfigProvider.get_configured_llm(streaming=False, config=self.config)
            response = await model.ainvoke([HumanMessage(content=prompt)])
            tokens = extract_tokens_from_message(response)
            self.record_llm_token_usage(
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                event_type="thought",
                model=str(getattr(model, "model_name", self.config.model_name) or ""),
                tool_name="schema_keyword_planner",
            )
            content = (getattr(response, "content", "") or "").strip()
            data = {}
            try:
                data = json.loads(content)
            except Exception:
                match = re.search(r"\{.*\}", content, flags=re.DOTALL)
                if match:
                    data = json.loads(match.group())
            keywords = str(data.get("keywords") or "").strip()
            if self._is_invalid_schema_search_keywords(keywords):
                return fallback_query
            return keywords[:300]
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to plan schema search keywords: %s", e)
            return fallback_query

    @staticmethod
    def _is_invalid_schema_search_keywords(keywords: str) -> bool:
        normalized = re.sub(r"\s+", "", str(keywords or "")).strip().lower()
        if not normalized:
            return True
        return normalized in {
            "...", "…", "keyword", "keywords", "关键词", "问题关键词",
            "n/a", "na", "none", "null", "无",
        }

    async def _auto_invoke_get_dataset_schema(
        self,
        *,
        keywords: str,
        tools: list[RuntimeToolSpec],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """新查数路径在 ReAct 开始前平台侧自动执行 get_dataset_schema。"""
        schema_spec = next((tool for tool in tools if tool.name == "get_dataset_schema"), None)
        if schema_spec is None:
            logger.warning("[DataAgentRunner] get_dataset_schema tool missing; skip auto prefetch")
            return

        tool_id = f"schema_prefetch_{uuid.uuid4().hex[:8]}"
        started_at = time.time()
        applied_keywords = str(keywords or "").strip()
        yield {
            "type": "log",
            "id": tool_id,
            "title": "自动获取数据集定义",
            "details": f"平台自动调用 get_dataset_schema(keywords={keywords or 'None'})",
            "status": "pending",
            "category": "tool",
            "started_at": int(started_at * 1000),
        }

        output = ""
        try:
            result = schema_spec.callable(keywords=keywords or None)
            if inspect.isawaitable(result):
                result = await result
            output = str(result or "")
        except Exception as exc:
            logger.error("[DataAgentRunner] Auto get_dataset_schema failed: %s", exc)
            output = f"[TOOL_ERROR] 自动获取数据集定义失败: {exc}"

        preview_state = _DataRunState()
        preview_state.last_schema_tool_keywords = applied_keywords
        self._apply_schema_tool_result(preview_state, output)
        yield {
            "type": "log",
            "id": tool_id,
            "title": "工具完成: get_dataset_schema",
            "details": self._format_tool_details(
                "get_dataset_schema",
                output,
                preview_state,
                {"keywords": keywords},
            ),
            "status": "error" if self._is_schema_fatal(preview_state) else "success",
            "category": "tool",
            "execution_time_ms": (time.time() - started_at) * 1000,
        }
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type="tool_call",
                agent_name=self.config.agent_name,
                model=self.config.model_name,
                temperature=float(self.config.temperature or 0),
                tool_name="get_dataset_schema",
                tool_input={"keywords": keywords},
                tool_output=output,
                raw_log=output[:4000],
                execution_time_ms=(time.time() - started_at) * 1000,
                timestamp=datetime.fromtimestamp(started_at),
            )
        )
        yield {"__schema_output__": output}

    def _should_rewrite_contextual_new_data_query(
        self,
        user_question: str,
        runtime_messages: List[Any],
    ) -> bool:
        q = (user_question or "").strip()
        if not q:
            return False
        prior_messages = [
            message
            for message in runtime_messages[:-1]
            if isinstance(message, (HumanMessage, AIMessage)) and getattr(message, "content", None)
        ]
        if not prior_messages:
            return False

        q_lower = q.lower()
        context_markers = [
            "那", "这个", "那个", "它", "其", "刚才", "上面", "上一轮", "前面", "之前",
            "本月呢", "上月呢", "今天呢", "昨天呢", "本周呢", "上周呢",
            "再按", "再看", "再查", "换成", "改成", "只看", "只查", "也看", "也查",
            "then", "this", "that", "it", "previous", "last one", "what about",
            "还是", "刚才的", "之前那个", "不是", "用这个", "数据查询", "数据需求", "查数据", "查数", "数据智能体"
        ]
        if any(marker in q_lower for marker in context_markers):
            return True
        query_verbs = ["查询", "查", "统计", "列出", "展示", "显示", "获取", "select", "show", "list"]
        return len(q) < 12 and not any(verb in q_lower for verb in query_verbs)

    async def _resolve_standalone_query_for_new_data_query(
        self,
        user_question: str,
        runtime_messages: List[Any],
    ) -> str:
        q = (user_question or "").strip()

        system_prompt = getattr(self.config, "system_prompt", None)
        if not isinstance(system_prompt, str):
            system_prompt = ""
        ltm_match = re.search(r"(\[Memory Profile\][\s\S]*?)(?=\n\n\[|\Z)", system_prompt)
        ltm_context = ltm_match.group(1).strip() if ltm_match else ""
        has_ltm = bool(ltm_context.replace("[Memory Profile]", "").strip()) if ltm_context else False

        need_rewrite = has_ltm or self._should_rewrite_contextual_new_data_query(q, runtime_messages)
        if not q or not need_rewrite:
            return q

        recent_history = []
        for message in runtime_messages[-7:-1]:
            if not isinstance(message, (HumanMessage, AIMessage)):
                continue
            content = getattr(message, "content", "") or ""
            if not isinstance(content, str):
                content = str(content)
            content = re.sub(r"\s+", " ", content).strip()
            if not content:
                continue
            role = "用户" if isinstance(message, HumanMessage) else "助手"
            recent_history.append(f"{role}: {content[:220]}")

        if not recent_history and not has_ltm:
            return q

        instructions = [
            "1. 只补全上下文缺失的查询对象、指标、维度、时间范围或筛选条件。",
            "2. 必须保留最新提问新增或修改的条件。",
            "3. 不要生成 SQL，不要选择表名/字段名，不要解释。",
            "4. 【纠错与澄清回溯】：如果【最新提问】本身没有具体的数据查询业务诉求，而只是为了纠正意图、强调是数据查询、或澄清需求（例如：'还是数据查询需求'、'不对，是查数'、'用数据查'、'重新用数据查一下'），说明它是一个‘意图校准信号’。此时，你必须回溯并找出最近对话中【用户上一次提出的真实查询诉求】（即被系统误判的那个真实业务问题，如'查一下我的信息'），并结合更前文的历史数据背景（如'统计近一年入职员工数据'），合并生成一个独立的、完整的业务查数问题。",
            "5. 如果无法可靠补全，原样返回最新提问。",
        ]
        ltm_section = ""
        if has_ltm:
            instructions.append("6. 结合【用户个性化偏好与记忆】，将最新提问中的俗称、别名、旧称转换为对应的标准名称。")
            ltm_section = f"\n\n【用户个性化偏好与记忆】\n{ltm_context}"

        time_anchor = build_data_query_time_anchor_block()
        prompt = (
            "你是 ChatBI 查询改写器。请根据最近对话和用户偏好，把【最新提问】改写成一句独立、完整、适合检索元数据和历史 SQL 案例的查数问题。\n"
            "要求：\n"
            + "\n".join(instructions)
            + ltm_section
            + f"\n\n{time_anchor}"
        )
        if recent_history:
            prompt += "\n\n【最近对话】\n" + "\n".join(recent_history)
        prompt += f"\n\n【最新提问】\n{q}\n\n【改写后的独立查数问题】"

        try:
            model = await AgentConfigProvider.get_configured_llm(streaming=False, config=self.config)
            response = await model.ainvoke([HumanMessage(content=prompt)])
            tokens = extract_tokens_from_message(response)
            self.record_llm_token_usage(
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                event_type="thought",
                model=str(getattr(model, "model_name", self.config.model_name) or ""),
                tool_name="standalone_query_rewrite",
            )
            rewritten = (getattr(response, "content", "") or "").strip().strip('"').strip("'")
            if not rewritten:
                return q
            return rewritten[:300]
        except Exception as e:
            logger.warning("[DataAgentRunner] Failed to rewrite standalone data query: %s", e)
            return q

    async def _generate_clarification_content(
        self,
        *,
        user_question: str,
        history: List[Dict[str, str]],
        reasoning: str,
    ) -> str:
        history_excerpt = DataQueryPrompts.format_clarification_history(history)
        user_profile = None
        if self.user_info:
            from app.services.ai.agent_prompts import AgentServicePrompts
            raw_name = self.user_info.get("user_name") or self.user_info.get("username", "Unknown User")
            user_id = str(self.user_info.get("user_id") or self.user_info.get("id") or "")
            real_name = self.user_info.get("real_name") or raw_name
            dept = self.user_info.get("dept_name") or self.user_info.get("department")
            org_path = self.user_info.get("org_path")
            dept_code = self.user_info.get("dept_code")
            role = self.user_info.get("role_name") or self.user_info.get("role")
            
            user_profile = AgentServicePrompts.user_context_message(
                user_id=user_id or "unknown",
                raw_name=raw_name,
                real_name=real_name,
                dept=dept,
                dept_code=dept_code,
                org_path=org_path,
                role=role,
            )

        fallback = DataQueryPrompts.build_clarification_fallback(
            user_question,
            reasoning,
            history_excerpt,
        )
        try:
            llm = await AgentConfigProvider.get_configured_llm(
                streaming=False,
                config=self.config,
            )
            chat_client = chat_client_from_handle(llm)
            content = await chat_client.generate_text(
                system_user_prompt_messages(
                    DataQueryPrompts.clarification_generation_prompt(
                        user_question,
                        reasoning,
                        history_excerpt,
                        user_profile=user_profile,
                    ),
                    user_prompt=user_question,
                )
            )
            cleaned = str(content or "").strip()
            if cleaned and DataQueryPrompts.has_quick_suggestions(cleaned):
                return finalize_visible_reply(
                    DataQueryPrompts.ensure_clarification_reason_block(
                        cleaned,
                        user_question,
                        reasoning,
                    ),
                    collapse_duplicates=False,
                )
            if cleaned:
                merged = DataQueryPrompts.append_contextual_quick_suggestions(
                    cleaned,
                    user_question,
                    reasoning,
                    history_excerpt,
                )
                if DataQueryPrompts.has_quick_suggestions(merged):
                    return finalize_visible_reply(
                        DataQueryPrompts.ensure_clarification_reason_block(
                            merged,
                            user_question,
                            reasoning,
                        ),
                        collapse_duplicates=False,
                    )
        except Exception as e:
            logger.warning("[DataAgentRunner] Contextual clarification generation failed: %s", e)
        return finalize_visible_reply(
            DataQueryPrompts.ensure_clarification_reason_block(
                fallback,
                user_question,
                reasoning,
            ),
            collapse_duplicates=False,
        )

    async def _yield_contextual_clarification(
        self,
        *,
        user_question: str,
        history: List[Dict[str, str]],
        reasoning: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {
            "type": "log",
            "id": f"clarify_{uuid.uuid4().hex[:8]}",
            "title": "需要补充查数信息",
            "details": reasoning,
            "status": "warning",
            "category": "intent",
        }
        content = await self._generate_clarification_content(
            user_question=user_question,
            history=history,
            reasoning=reasoning,
        )
        yield {"content": content, "status": "success"}

    async def _yield_missing_reusable_result_clarification(
        self,
        history: List[Dict[str, str]],
        *,
        user_question: str = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        history_excerpt = DataQueryPrompts.format_clarification_history(history)
        reasoning = (
            "检测到本轮是基于上一轮结果的分析/可视化请求，"
            "但当前会话没有保存的结构化查询结果。"
        )
        yield {
            "type": "log",
            "id": f"reuse_miss_{uuid.uuid4().hex[:8]}",
            "title": "缺少可复用查询结果",
            "details": reasoning,
            "status": "error",
        }
        yield {
            "content": DataQueryPrompts.build_missing_reusable_result_fallback(
                history_excerpt,
                user_question=user_question,
            ),
            "status": "success",
        }

    async def _synthesize_from_last_data_result(
        self,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
        last_result: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        start_synthesis = time.time()
        yield {
            "type": "log",
            "id": f"reuse_{uuid.uuid4().hex[:8]}",
            "title": "复用上一轮查询结果",
            "details": "检测到本轮是基于上一轮结果的分析/可视化请求，已跳过重新检索 Schema 与执行 SQL。",
            "status": "success",
        }
        yield {"type": "thinking", "status": "continuing"}

        prompt_without_menu = (system_prompt or "").replace(
            "{dataset_menu}",
            DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
        )
        safe_result = dict(last_result)
        for r_key in ("rows", "items", "data", "records"):
            val = safe_result.get(r_key)
            if isinstance(val, list) and len(val) > 50:
                safe_result[r_key] = val[:50]
                safe_result["_display_note"] = "部分明细数据由于上下文长度限制已在此处被省略..."
                break
        result_json = json.dumps(safe_result, ensure_ascii=False, indent=2)

        from app.services.ai.runtime.agentscope.compat import HumanMessage

        synthesis_messages = [SystemMessage(content=prompt_without_menu)]
        # 只保留用户追问，不把上一轮 assistant 全文（含图表/表格）塞进 prompt，避免模型照抄两遍。
        synthesis_messages.extend(
            message
            for message in runtime_messages[-6:-1]
            if isinstance(message, HumanMessage) and getattr(message, "content", None)
        )
        synthesis_messages.append(
            HumanMessage(content=DataQueryPrompts.followup_synthesis_user_message(user_question, result_json))
        )

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        full_synthesis_content = ""
        content_emitted = False
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        last_synthesis_chunk = None
        try:
            async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
                last_synthesis_chunk = chunk
                content = str(getattr(chunk, "content", "") or "")
                if not content:
                    continue
                if not content_emitted:
                    generation_start = time.time()
                    content_emitted = True
                    yield {
                        "type": "log",
                        "id": gen_log_id,
                        "title": "✨ 开始生成回复",
                        "status": "pending",
                        "started_at": int(generation_start * 1000),
                    }
                full_synthesis_content += content
                yield {"content": content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error("[DataAgentRunner] Follow-up synthesis failed: %s", syn_err)
            fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
            full_synthesis_content = fallback
            yield {
                "type": "log",
                "id": f"syn_err_{uuid.uuid4().hex[:6]}",
                "title": "⚠️ 总结生成失败",
                "details": str(syn_err),
                "status": "error",
            }
            yield {"content": fallback}

        deduped_synthesis = finalize_visible_reply(full_synthesis_content)
        if deduped_synthesis != full_synthesis_content:
            logger.warning(
                "[DataAgentRunner] Collapsed duplicated follow-up synthesis output (len %s -> %s)",
                len(full_synthesis_content),
                len(deduped_synthesis),
            )
            full_synthesis_content = deduped_synthesis
            if content_emitted:
                yield {"type": "retraction", "content": full_synthesis_content}

        synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=str(getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)),
                temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
                tool_output={"content": full_synthesis_content, "reused_last_data_result": True},
                raw_log=full_synthesis_content,
                prompt_tokens=synthesis_tokens["prompt_tokens"],
                completion_tokens=synthesis_tokens["completion_tokens"],
                total_tokens=synthesis_tokens["total_tokens"],
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                timestamp=datetime.fromtimestamp(start_synthesis),
            )
        )

    async def _synthesize_from_history_data_result(
        self,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
        history: List[Dict[str, str]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        start_synthesis = time.time()
        yield {
            "type": "log",
            "id": f"reuse_hist_{uuid.uuid4().hex[:8]}",
            "title": "复用上一轮查询结果",
            "details": (
                "检测到本轮是基于上一轮结果的分析/可视化请求；结构化缓存暂不可用，"
                "已基于最近对话中的查数展示继续处理。"
            ),
            "status": "success",
        }
        yield {"type": "thinking", "status": "continuing"}

        history_excerpt = self._latest_data_assistant_excerpt(history)
        prompt_without_menu = (system_prompt or "").replace(
            "{dataset_menu}",
            DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
        )

        from app.services.ai.runtime.agentscope.compat import HumanMessage

        synthesis_messages = [SystemMessage(content=prompt_without_menu)]
        synthesis_messages.extend(
            message
            for message in runtime_messages[-6:-1]
            if isinstance(message, HumanMessage) and getattr(message, "content", None)
        )
        synthesis_messages.append(
            HumanMessage(
                content=DataQueryPrompts.followup_synthesis_from_history_user_message(
                    user_question,
                    history_excerpt,
                )
            )
        )

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        full_synthesis_content = ""
        content_emitted = False
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        last_synthesis_chunk = None
        try:
            async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
                last_synthesis_chunk = chunk
                content = str(getattr(chunk, "content", "") or "")
                if not content:
                    continue
                if not content_emitted:
                    generation_start = time.time()
                    content_emitted = True
                    yield {
                        "type": "log",
                        "id": gen_log_id,
                        "title": "✨ 开始生成回复",
                        "status": "pending",
                        "started_at": int(generation_start * 1000),
                    }
                full_synthesis_content += content
                yield {"content": content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error("[DataAgentRunner] History follow-up synthesis failed: %s", syn_err)
            fallback = DataQueryPrompts.FOLLOWUP_SYNTHESIS_FALLBACK
            full_synthesis_content = fallback
            yield {
                "type": "log",
                "id": f"syn_err_{uuid.uuid4().hex[:6]}",
                "title": "⚠️ 总结生成失败",
                "details": str(syn_err),
                "status": "error",
            }
            yield {"content": fallback}

        deduped_synthesis = finalize_visible_reply(full_synthesis_content)
        if deduped_synthesis != full_synthesis_content:
            full_synthesis_content = deduped_synthesis
            if content_emitted:
                yield {"type": "retraction", "content": full_synthesis_content}

        synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=str(getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)),
                temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
                tool_output={"content": full_synthesis_content, "reused_history_data_result": True},
                raw_log=full_synthesis_content,
                prompt_tokens=synthesis_tokens["prompt_tokens"],
                completion_tokens=synthesis_tokens["completion_tokens"],
                total_tokens=synthesis_tokens["total_tokens"],
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                timestamp=datetime.fromtimestamp(start_synthesis),
            )
        )

    async def _synthesize_from_cached_sql_result(
        self,
        *,
        runtime_messages: List[Any],
        system_prompt: str,
        user_question: str,
        state: _DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        start_synthesis = time.time()
        yield {
            "type": "log",
            "id": f"repeat_sql_{uuid.uuid4().hex[:8]}",
            "title": "复用已执行 SQL 结果",
            "details": "检测到模型重复调用相同 SQL。平台已拦截重复执行，并基于首次成功查询结果生成最终回答。",
            "status": "success",
        }

        raw_result = state.last_successful_sql_output
        parsed_result = self._try_parse_json_output(raw_result)
        result_json = json.dumps(parsed_result, ensure_ascii=False, indent=2, default=str)
        if len(result_json) > 20000:
            result_json = result_json[:20000] + "\n... [SQL 结果过长已截断]"

        execution_review = (
            "【执行过程回顾】\n"
            "- 已成功执行 SQL 并获得非空结果。\n"
            "- 随后模型重复调用相同 SQL，平台已拦截重复执行并复用首次成功查询结果。\n\n"
            "【查询结果】\n"
            f"{result_json}"
        )
        prompt_without_menu = (system_prompt or "").replace(
            "{dataset_menu}",
            DataQueryPrompts.REUSE_DATASET_MENU_PLACEHOLDER,
        )
        synthesis_messages = [SystemMessage(content=prompt_without_menu)]
        synthesis_messages.extend(
            message
            for message in runtime_messages[-6:-1]
            if isinstance(message, HumanMessage) and getattr(message, "content", None)
        )
        synthesis_messages.append(
            HumanMessage(content=DataQueryPrompts.synthesis_user_message(user_question, execution_review))
        )

        final_llm = await AgentConfigProvider.get_synthesis_llm(streaming=True, config=self.config)
        full_synthesis_content = ""
        content_emitted = False
        generation_start = None
        gen_log_id = f"gen_{uuid.uuid4().hex[:8]}"
        last_synthesis_chunk = None
        try:
            async for chunk in final_llm.astream(normalize_messages_for_llm(synthesis_messages)):
                last_synthesis_chunk = chunk
                content = str(getattr(chunk, "content", "") or "")
                if not content:
                    continue
                if not content_emitted:
                    generation_start = time.time()
                    content_emitted = True
                    yield {
                        "type": "log",
                        "id": gen_log_id,
                        "title": "✨ 开始生成回复",
                        "status": "pending",
                        "started_at": int(generation_start * 1000),
                    }
                full_synthesis_content += content
                yield {"content": content}
            if generation_start:
                yield {
                    "type": "log",
                    "id": gen_log_id,
                    "title": "✨ 生成回复完成",
                    "status": "success",
                    "execution_time_ms": (time.time() - generation_start) * 1000,
                }
        except Exception as syn_err:
            logger.error("[DataAgentRunner] Cached SQL synthesis failed: %s", syn_err)
            fallback = DataQueryPrompts.SYNTHESIS_FAILED_FALLBACK
            full_synthesis_content = fallback
            yield {
                "type": "log",
                "id": f"syn_err_{uuid.uuid4().hex[:6]}",
                "title": "⚠️ 总结生成失败",
                "details": str(syn_err),
                "status": "error",
            }
            yield {"content": fallback}

        deduped_synthesis = finalize_visible_reply(full_synthesis_content)
        if deduped_synthesis != full_synthesis_content:
            logger.warning(
                "[DataAgentRunner] Collapsed duplicated cached SQL synthesis output (len %s -> %s)",
                len(full_synthesis_content),
                len(deduped_synthesis),
            )
            full_synthesis_content = deduped_synthesis
            if content_emitted:
                yield {"type": "retraction", "content": full_synthesis_content}

        synthesis_tokens = extract_tokens_from_message(last_synthesis_chunk)
        self._increment_step()
        self.trace_buffer.append(
            AgentExecutionStep(
                step_number=self.step_counter,
                event_type="synthesis",
                agent_name=self.config.agent_name,
                model=str(getattr(final_llm, "model_name", self.config.synthesis_model_name or self.config.model_name)),
                temperature=float(self.config.synthesis_temperature or self.config.temperature or 0),
                tool_output={"content": full_synthesis_content, "reused_repeated_sql_result": True},
                raw_log=full_synthesis_content,
                prompt_tokens=synthesis_tokens["prompt_tokens"],
                completion_tokens=synthesis_tokens["completion_tokens"],
                total_tokens=synthesis_tokens["total_tokens"],
                execution_time_ms=(time.time() - start_synthesis) * 1000,
                timestamp=datetime.fromtimestamp(start_synthesis),
            )
        )

    async def _build_system_content(
        self,
        *,
        context_action_result: Optional[Dict[str, Any]] = None,
        include_context_action: bool = False,
    ) -> str:
        system_prompt = self.config.system_prompt or ""
        if "{dataset_menu}" in system_prompt:
            user_id = self.user_info.get("user_id") if self.user_info else None
            is_admin = self.user_info.get("role") == "admin" if self.user_info else False
            dataset_menu = await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)
            system_prompt = system_prompt.replace("{dataset_menu}", dataset_menu)
        context_action_prompt = ""
        if include_context_action:
            result_json = ""
            if context_action_result:
                result_json = json.dumps(context_action_result, ensure_ascii=False)
                if len(result_json) > 20000:
                    result_json = result_json[:20000] + "\n... [上一轮结果过长已截断]"
            context_action_prompt = f"\n\n{DataQueryPrompts.context_action_guide(result_json)}"
        time_anchor = build_data_query_time_anchor_block()
        return (
            f"{DataQueryPrompts.GLOBAL_GUARDRAILS}\n\n"
            f"{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}\n\n"
            f"{DataQueryPrompts.SQL_PLAN_ENFORCEMENT + chr(10) + chr(10) if self._is_sql_plan_enabled() else ''}"
            f"{time_anchor}\n\n"
            f"{DataQueryPrompts.FOLLOWUP_REUSE_CONSTRAINT}\n\n"
            f"{system_prompt}"
            f"{context_action_prompt}"
        )

    @staticmethod
    def _data_run_state_to_pending_state(
        state: _DataRunState,
        stream_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        from dataclasses import asdict

        return {
            **stream_meta,
            "data_run_state": asdict(state),
        }

    @staticmethod
    def _sync_pending_data_run_state(
        state: _DataRunState,
        pending_state: Dict[str, Any],
    ) -> None:
        from dataclasses import asdict

        pending_state["data_run_state"] = asdict(state)

    @staticmethod
    def _build_stream_state(
        state: _DataRunState,
        stream_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        stream_state = DataAgentRunner._data_run_state_to_pending_state(state, stream_meta)
        stream_state["tool_names"] = state.tool_names
        stream_state["tool_args_text"] = state.tool_args_text
        stream_state["tool_outputs"] = state.tool_outputs
        stream_state["tool_started_at"] = state.tool_started_at
        stream_state.setdefault("tool_data", {})
        return stream_state

    @staticmethod
    def _pending_state_to_data_run_state(pending_state: Dict[str, Any]) -> tuple[_DataRunState, Dict[str, Any]]:
        from dataclasses import fields

        raw = pending_state.get("data_run_state") or {}
        valid_keys = {field.name for field in fields(_DataRunState)}
        kwargs = {key: raw[key] for key in valid_keys if key in raw}
        detector_raw = kwargs.get("tool_loop_detector")
        if isinstance(detector_raw, dict):
            detector_keys = {field.name for field in fields(ToolLoopDetector)}
            try:
                kwargs["tool_loop_detector"] = ToolLoopDetector(
                    **{key: detector_raw[key] for key in detector_keys if key in detector_raw}
                )
            except Exception:
                kwargs["tool_loop_detector"] = ToolLoopDetector()
        data_state = _DataRunState(**kwargs)
        stream_meta = {
            key: pending_state[key]
            for key in ("system_content", "max_steps")
            if key in pending_state
        }
        return data_state, stream_meta

    async def _stream_agentscope_events(
        self,
        *,
        event_stream: Any,
        agent: Any | None = None,
        tools: list[RuntimeToolSpec],
        native_model: Any,
        state: _DataRunState | None = None,
        stream_meta: Dict[str, Any] | None = None,
        emit_final_guard: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        state = state or _DataRunState()
        stream_meta = stream_meta or {}
        self._last_run_state = state
        stream_state = self._build_stream_state(state, stream_meta)

        async def on_before_pending_interrupt(pending_state: Dict[str, Any]) -> None:
            self._sync_pending_data_run_state(state, pending_state)

        async def on_tool_result_end(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            tool_id = getattr(event, "tool_call_id", "")
            tool_name = state.tool_names.get(tool_id, "")
            raw_args = state.tool_args_text.get(tool_id, "") or "{}"
            try:
                tool_args = json.loads(raw_args)
            except Exception:
                tool_args = {"input": raw_args}
            output = state.tool_outputs.get(tool_id, "")
            duration_ms = (time.time() - state.tool_started_at.get(tool_id, time.time())) * 1000
            if tool_name == "get_dataset_schema":
                self._record_schema_keywords(state, tool_args)
                self._apply_schema_tool_result(state, output)
                if state.schema_miss:
                    self._prepare_controlled_schema_retry_keywords(
                        state,
                        str(stream_meta.get("user_question") or ""),
                    )
            elif tool_name == "execute_sql_query":
                parsed_output, should_save_followup = self._apply_sql_tool_result(
                    state,
                    tool_args=tool_args,
                    output=output,
                )
                if should_save_followup:
                    await self._save_last_data_result_for_followups(tool_args, parsed_output)
            self._record_tool_call_signature(state, tool_name, tool_args)
            state.halt_current_react = (
                state.sql_error
                or state.empty_sql_result
                or state.sql_plan_missing
                or state.sql_static_risk
                or state.sql_repeat_gate_block
                or state.failed_sql_repeat_gate_block
                or state.ratio_anomaly
                or state.duration_anomaly
                or state.diagnostic_sql_pending_final
                or state.tool_loop_fuse_triggered
            )
            self._sync_pending_data_run_state(state, stream_state)
            self._increment_step()
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self.step_counter,
                    event_type="tool_call",
                    agent_name=self.config.agent_name,
                    model=getattr(native_model, "model", self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_name=tool_name,
                    tool_input=tool_args,
                    tool_output=output,
                    raw_log=str(output),
                    execution_time_ms=duration_ms,
                    timestamp=datetime.fromtimestamp(state.tool_started_at.get(tool_id, time.time())),
                )
            )
            yield {
                "type": "log",
                "id": tool_id,
                "title": f"工具完成: {tool_name}",
                "details": self._format_tool_details(tool_name, output, state, tool_args),
                "status": "success",
                "execution_time_ms": duration_ms,
            }

        def track_sql_plan_delta(delta: str) -> None:
            state.text_window = (state.text_window + delta)[-4000:]
            if self._has_sql_plan(state.text_window):
                if not state.sql_plan_seen:
                    state.sql_plan_seen = True
                    self._sync_pending_data_run_state(state, stream_state)

        async def on_text_block_delta(event: Any) -> AsyncGenerator[Dict[str, Any], None]:
            block_id = str(getattr(event, "block_id", "") or "")
            if block_id:
                state.active_text_block_id = block_id
            if state.ignore_text_block:
                return
            delta = str(getattr(event, "delta", ""))
            track_sql_plan_delta(delta)
            if not state.ready_to_answer:
                state.blocked_content += delta
                return
            if not state.content_emitted:
                state.content_emitted = True
                yield {
                    "type": "log",
                    "id": f"gen_data_{uuid.uuid4().hex[:8]}",
                    "title": "✨ 开始生成回复",
                    "status": "success",
                }
            state.full_content += delta
            state.current_text_block_emitted = True
            yield {"content": delta}

        async for event in event_stream:
            event_type = str(getattr(event, "type", ""))
            if event_type == "MODEL_CALL_END":
                self._record_agent_scope_model_call(
                    event,
                    state=stream_state,
                    native_model=native_model,
                )
            if event_type == "THINKING_BLOCK_DELTA":
                track_sql_plan_delta(str(getattr(event, "delta", "")))
            if event_type == "TOOL_CALL_START":
                state.text_blocks_emitted_since_last_tool = 0
                state.ignore_text_block = False
                state.current_text_block_emitted = False

            if event_type == "TEXT_BLOCK_START":
                block_id = str(getattr(event, "block_id", "") or "")
                if block_id:
                    state.active_text_block_id = block_id
                state.current_text_block_emitted = False
                state.ignore_text_block = (
                    state.ready_to_answer
                    and state.text_blocks_emitted_since_last_tool >= 1
                )
                continue

            if event_type == "TEXT_BLOCK_END":
                if state.current_text_block_emitted and state.ready_to_answer:
                    state.text_blocks_emitted_since_last_tool += 1
                state.current_text_block_emitted = False
                continue

            async for chunk in map_standard_agentscope_event(
                event,
                state=stream_state,
                on_tool_result_end=on_tool_result_end,
                on_text_block_delta=on_text_block_delta,
                on_before_pending_interrupt=on_before_pending_interrupt,
                agent=agent,
                runner=self,
                tools=tools,
                native_model=native_model,
                agent_name=self._runtime_agent_name(),
            ):
                yield chunk
                if is_interrupt_sse_chunk(chunk):
                    return
            if state.sql_fatal_error:
                logger.info("[DataAgentRunner] Fatal SQL error detected during ReAct. Terminating execution immediately.")
                async for chunk in self._yield_sql_fatal_abort(state):
                    yield chunk
                return
            if state.halt_current_react:
                logger.info("[DataAgentRunner] SQL result requires repair. Stopping current ReAct stream.")
                break

        if emit_final_guard:
            guard_emitted = False
            async for chunk in self._emit_final_guard(state):
                guard_emitted = True
                yield chunk
            if guard_emitted:
                return

        if state.full_content:
            self._increment_step()
            self.trace_buffer.append(
                AgentExecutionStep(
                    step_number=self.step_counter,
                    event_type="synthesis",
                    agent_name=self.config.agent_name,
                    model=getattr(native_model, "model", self.config.model_name),
                    temperature=float(self.config.temperature or 0),
                    tool_output={"content": state.full_content},
                    raw_log=state.full_content,
                    execution_time_ms=(time.time() - state.start_synthesis) * 1000,
                    timestamp=datetime.fromtimestamp(state.start_synthesis),
                )
            )

    @staticmethod
    def _is_schema_fatal(state: _DataRunState) -> bool:
        return (
            state.schema_service_unavailable
            or state.no_authorized_schema
            or state.rag_not_synced
            or state.schema_miss_count >= 2  # 连续两次未命中（含换词重试）→ 硬终止
        )

    def _schema_fatal_response(self, state: _DataRunState) -> tuple[str, str]:
        if state.schema_service_unavailable:
            return (
                "元数据服务不可用",
                DataQueryPrompts.SCHEMA_SERVICE_UNAVAILABLE_CONTENT,
            )
        if state.no_authorized_schema:
            return (
                "无授权数据集",
                DataQueryPrompts.NO_AUTHORIZED_SCHEMA_CONTENT,
            )
        if state.rag_not_synced:
            return (
                "元数据未同步知识库",
                DataQueryPrompts.RAG_NOT_SYNCED_CONTENT,
            )
        if state.schema_miss_count >= 2:
            return (
                "连续未命中数据集定义",
                DataQueryPrompts.SCHEMA_MISS_EXHAUSTED_CONTENT,
            )
        return (
            "Schema 获取失败",
            DataQueryPrompts.SCHEMA_SERVICE_UNAVAILABLE_CONTENT,
        )

    async def _yield_sql_fatal_abort(
        self,
        state: _DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if state.sql_fatal_emitted:
            return
        presentation = map_sql_tool_error_for_user(state.sql_fatal_message)
        state.sql_fatal_emitted = True
        yield {
            "type": "log",
            "id": f"fatal_sql_{uuid.uuid4().hex[:8]}",
            "title": presentation.title,
            "details": truncate_for_context(str(state.sql_fatal_message or ""), max_len=1000)
            or presentation.title,
            "status": "error",
        }
        yield {
            "content": presentation.content,
            "status": "error",
        }

    async def _yield_schema_fatal_abort(
        self,
        state: _DataRunState,
        details: Any = "",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        title, content = self._schema_fatal_response(state)
        yield {
            "type": "log",
            "id": f"schema_fatal_{uuid.uuid4().hex[:8]}",
            "title": title,
            "details": truncate_for_context(str(details or ""), max_len=1000) or title,
            "status": "error",
        }
        yield {
            "content": content,
            "status": "error",
        }

    async def _emit_final_guard(
        self,
        state: _DataRunState,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        has_guard_condition = (
            bool(state.blocked_content)
            or state.sql_before_schema
            or state.sql_error
            or state.empty_sql_result
            or state.sql_static_risk
            or state.failed_sql_repeat_gate_block
            or state.ratio_anomaly
            or state.duration_anomaly
            or state.diagnostic_sql_pending_final
            or state.tool_loop_fuse_triggered
            or self._is_schema_fatal(state)
        )
        if state.full_content or state.ready_to_answer or not has_guard_condition:
            return
        if self._is_schema_fatal(state):
            _, content = self._schema_fatal_response(state)
        elif (
            state.requires_fresh_data
            and state.requires_sql_query
            and not state.sql_completed
            and (
                state.schema_miss_count >= 2
                or (not state.schema_completed and state.schema_miss_count >= 1)
            )
        ):
            content = (
                DataQueryPrompts.SCHEMA_MISS_EXHAUSTED_CONTENT
                if state.schema_miss_count >= 2
                else DataQueryPrompts.SCHEMA_MISS_ABORT_CONTENT
            )
        elif state.sql_before_schema:
            content = "为保证数据准确性，请先检索数据集定义后再执行数据查询。"
        elif state.sql_error:
            error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
            presentation = map_sql_tool_error_for_user(error_text)
            content = presentation.content
        elif state.failed_sql_repeat_gate_block:
            error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
            presentation = map_sql_tool_error_for_user(error_text)
            content = presentation.content
        elif state.diagnostic_sql_pending_final:
            content = (
                "诊断查询已返回样本数据，但尚未完成最终业务 SQL 复核，暂时无法生成结论。\n\n"
                "💡 **建议您可以尝试**：\n"
                "1. 稍微调整筛选条件或时间范围后重新提问。\n"
                "2. 若问题较复杂，可拆成更具体的单指标/单表问题。"
            )
        elif state.empty_sql_result:
            content = (
                "抱歉，系统在当前数据集中未查询到符合条件的数据。\n\n"
                "💡 **建议您可以尝试**：\n"
                "1. 检查提问中是否包含不正确的筛选条件（如时间范围不正确、拼写错误或不存在的项目名称）。\n"
                "2. 换个提问方式，或适当放宽时间范围重试（例如将精确时间改为模糊时间，如“本月”）。\n"
                "3. 确认当前选中的数据集是否包含您想查询的信息。"
            )
        elif state.sql_static_risk:
            content = (
                "该查询涉及的数据量过大或超出安全规范，已被系统自动拦截。\n\n"
                "💡 **建议您可以尝试**：\n"
                "1. 明确时间限制（如“查询最近3天”、“本周内”）。\n"
                "2. 避免使用过于宽泛的“全部”或“所有”类型查询，缩小范围后重试。"
            )
        elif state.ratio_anomaly:
            content = (
                "计算出的占比/比率数据疑似存在异常，为保证数据准确性，已自动拦截该次回答。\n\n"
                "💡 **建议您可以尝试**：\n"
                "1. 重新检查提问中涉及的比较口径（如“同比”、“环比”或时段先后顺序是否表述清晰）。\n"
                "2. 重新核对提问内容并以简化的表述重新提问。"
            )
        elif state.duration_anomaly:
            content = (
                "计算出的时延/时长数据疑似存在异常，为保证数据准确性，已自动拦截该次回答。\n\n"
                "💡 **建议您可以尝试**：\n"
                "1. 重新检查提问中涉及的时间字段方向、时区或单位定义。\n"
                "2. 重新核对提问内容并以简化的表述重新提问。"
            )
        elif state.tool_loop_fuse_triggered:
            content = f"检测到工具调用出现循环，已被系统安全中止。{state.tool_loop_fuse_reason}"
        else:
            content = "由于未能完成有效的数据检索和计算，无法为您生成准确的回答。建议您核对问题后重新提问。"
        yield {
            "type": "log",
            "id": f"data_guard_{uuid.uuid4().hex[:8]}",
            "title": "阻止未查数回答",
            "details": "模型在满足 ChatBI 查数顺序前尝试直接回答，已拦截该输出。",
            "status": "warning",
        }
        yield {
            "content": content,
            "status": "error",
        }

    async def _retract_provisional_content_before_repair(
        self,
        state: _DataRunState,
        *,
        reason: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not state.full_content:
            return
        logger.info(
            "[DataAgentRunner] Retracting provisional content before continuing repair: %s",
            reason,
        )
        state.full_content = ""
        state.content_emitted = False
        state.current_text_block_emitted = False
        state.text_blocks_emitted_since_last_tool = 0
        yield {
            "type": "retraction",
            "content": "",
            "final": False,
        }

    def _current_repair_kind(self, state: _DataRunState) -> str:
        if self._is_schema_fatal(state):
            return ""
        if state.schema_ambiguous:
            return "schema_ambiguous"
        if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
            return "schema_refresh_after_sql_error"
        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return "sql_before_schema"
        if state.schema_miss and not state.no_authorized_schema:
            return "schema_miss"
        if state.schema_needs_refinement:
            return "schema_refinement"
        if state.sql_static_risk:
            return "sql_static_risk"
        if state.sql_plan_missing:
            return "sql_plan_missing"
        if state.failed_sql_repeat_gate_block:
            return "failed_sql_repeat"
        if state.sql_error:
            return "sql_error"
        if state.empty_sql_result:
            return "empty_sql_result"
        if state.ratio_anomaly:
            return "ratio_anomaly"
        if state.duration_anomaly:
            return "duration_anomaly"
        if state.tool_loop_fuse_triggered:
            return "tool_loop_fuse"
        if state.diagnostic_sql_pending_final:
            return "diagnostic_sql_pending_final"
        if (
            state.requires_fresh_data
            and state.requires_sql_query
            and state.schema_completed
            and state.sql_plan_seen
            and not state.sql_completed
            and not state.ready_to_answer
        ):
            return "missing_sql"
        if (
            state.requires_fresh_data
            and state.blocked_content.strip()
            and not state.ready_to_answer
        ):
            if not state.schema_completed:
                return "missing_schema"
            if state.requires_sql_query and not state.sql_completed:
                return "missing_sql"
        return ""

    def _repair_budget_exhausted(self, state: _DataRunState) -> bool:
        kind = self._current_repair_kind(state)
        if not kind:
            return False
        budget = DATA_REPAIR_BUDGETS.get(kind, MAX_DATA_REPAIR_ROUNDS)
        return state.repair_attempts.get(kind, 0) >= budget

    def _record_repair_attempt(self, state: _DataRunState) -> None:
        kind = self._current_repair_kind(state)
        if not kind:
            return
        state.repair_attempts[kind] = state.repair_attempts.get(kind, 0) + 1

    def _build_repair_message(self, state: _DataRunState) -> str:
        if self._is_schema_fatal(state):
            return ""
        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return (
                "【Schema 顺序要求】本轮新数据查询必须先调用 get_dataset_schema 获取数据集定义，"
                "再调用 execute_sql_query。\n"
                f"{DataQueryPrompts.MUST_FETCH_SCHEMA}\n"
                "在获得有效 schema 前禁止生成或执行 SQL，也禁止直接回答用户。"
            )
        if state.schema_miss and not state.no_authorized_schema:
            controlled_hint = ""
            if state.controlled_schema_retry_keywords:
                controlled_hint = (
                    f"本次重试必须使用平台受控重试 keywords：{state.controlled_schema_retry_keywords}。"
                    "禁止另行发挥或改写为无关业务关键词。"
                )
            return (
                "【Schema 重试要求】上一轮 get_dataset_schema 未命中相关数据集定义。"
                f"{controlled_hint}"
                "请仅基于用户原问题中的业务对象、指标或维度重新调用 get_dataset_schema，"
                "禁止追加与业务对象无关的通用元数据词或其他系统关键词。"
                "在获得有效 schema 前禁止生成或执行 SQL，也禁止直接回答用户。"
            )
        if state.schema_needs_refinement:
            return (
                "【Schema 相关性不足】上一轮 get_dataset_schema 返回了低置信度或目录型结果，"
                "尚不足以可靠生成 SQL。请换用更具体的业务对象、指标、维度、系统名或同义词重新调用 get_dataset_schema。"
                "在获得相关性更明确的 schema 前禁止执行 SQL 或直接回答用户。"
            )
        if state.schema_ambiguous:
            return (
                "【Schema 歧义澄清要求】上一轮 get_dataset_schema 返回多个高置信度候选，"
                f"{state.schema_ambiguous_reason}。请停止生成 SQL，先用自然语言和 quick 按钮请用户确认"
                "具体数据集、指标口径或业务对象；确认前禁止执行 SQL。"
            )
        if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
            summary = (state.last_sql_error_summary or state.sql_error_message or "").strip()
            return (
                "【Schema 重查要求】上一轮 SQL 因字段/表/标识符引用错误失败，"
                f"错误信息：{summary[:800]}\n"
                f"{self._invalid_identifier_repair_hint(summary)}\n"
                f"{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}\n"
                "必须先重新调用 get_dataset_schema，核对物理列名、表名与 JOIN 键。"
                "在未完成 Schema 重查前禁止 execute_sql_query，也禁止原样重复失败 SQL。"
            )
        if state.sql_static_risk:
            return (
                "【SQL 静态风险修正要求】上一轮 execute_sql_query 被平台拦截，"
                f"原因：{state.sql_static_risk_reason}\n"
                "请修正 SQL 后重新调用 execute_sql_query，例如补充时间范围、限制返回行数、避免 SELECT *、"
                "或补齐 JOIN 条件。修正并执行成功前禁止直接回答用户。"
            )
        if state.sql_plan_missing:
            return (
                "【SQL Plan 补充要求】上一轮 execute_sql_query 因缺少结构化 SQL Plan 被平台拦截。\n"
                f"{DataQueryPrompts.HIGH_RISK_REQUIRE_PLAN}\n"
                f"{DataQueryPrompts.SQL_PLAN_ENFORCEMENT}\n"
                "请先输出完整 <sql_plan>{...}</sql_plan>，然后基于同一计划修正并调用 execute_sql_query。"
                "禁止跳过计划直接查数。"
            )
        if state.failed_sql_repeat_gate_block:
            error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
            repair = (
                "【重复失败 SQL 修正要求】上一轮尝试原样重复执行已经失败的 SQL，已被平台拦截。"
                f"原始数据库错误：{error_text[:800]}\n"
                "请只围绕原始数据库错误修正 SQL 后再次调用 execute_sql_query；"
                "必须修改导致失败的字段名、表名、JOIN 条件、筛选条件、时间转换或聚合逻辑，"
                "禁止继续提交与上次完全相同的 SQL。SQL 成功前禁止直接回答用户。"
            )
            repair += self._sql_repair_taxonomy_hint(error_text)
            repair += self._invalid_identifier_repair_hint(error_text)
            if self._is_schema_reference_sql_error(error_text):
                repair += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
            if self._is_date_format_sql_error(error_text):
                repair += f"\n\n{DataQueryPrompts.DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE}"
            return repair
        if state.sql_error:
            error_text = (state.last_sql_error_summary or state.sql_error_message or "").strip()
            repair = (
                "【SQL 修正要求】上一轮 execute_sql_query 执行失败。"
                f"错误信息：{error_text[:800]}\n"
                "请基于已获得的 get_dataset_schema 结果修正 SQL，并再次调用 execute_sql_query。"
                "禁止原样重复提交与上次完全相同的失败 SQL。"
                "在 SQL 成功前禁止直接回答用户。"
            )
            repair += self._sql_repair_taxonomy_hint(error_text)
            if state.last_failed_sql_normalized:
                repair += (
                    "\n\n【禁止重复 SQL】上次失败 SQL 归一化后与当前尝试一致时，平台将直接拦截。"
                    "必须修改至少一处：列名、表名、JOIN、WHERE、时间范围或聚合逻辑。"
                )
            err_lower = error_text.lower()
            repair += self._invalid_identifier_repair_hint(error_text)
            if self._is_schema_reference_sql_error(error_text):
                repair += f"\n\n{DataQueryPrompts.SCHEMA_REFERENCE_SQL_ERROR_REPAIR_GUIDE}"
            if self._is_date_format_sql_error(error_text):
                repair += f"\n\n{DataQueryPrompts.DATE_FORMAT_SQL_ERROR_REPAIR_GUIDE}"
            if "invalid expression" in err_lower or "unexpected token" in err_lower:
                repair += f"\n\n{DataQueryPrompts.SQL_PAGINATION_SYNTAX_GUIDE}"
            return repair
        if state.empty_sql_result:
            return (
                "【空结果复查要求】上一轮 execute_sql_query 执行成功但返回空结果。"
                f"原因：{state.empty_sql_reason}\n"
                "请先用诊断 SQL 复查筛选值、时间范围、子查询或 JOIN 条件，再执行最终 SQL。"
                "在最终 SQL 返回有效结果前禁止直接回答用户。"
            )
        if state.ratio_anomaly:
            return DataQueryPrompts.ratio_anomaly_recheck(state.ratio_anomaly_reason)
        if state.duration_anomaly:
            return (
                "【时间差/时延结果异常复核】上一轮 execute_sql_query 返回了明显异常的时间差、"
                f"时延或时长字段。原因：{state.duration_anomaly_reason}\n"
                "请检查 SQL 中时间字段的相减方向、时区口径、now()/当前时间来源、单位换算（秒/毫秒/分钟/小时），"
                "修正 SQL 后重新调用 execute_sql_query。修正并执行成功前禁止直接回答用户。"
            )
        if state.tool_loop_fuse_triggered:
            return ""
        if state.diagnostic_sql_pending_final:
            return (
                "【最终 SQL 执行要求】上一轮 execute_sql_query 是诊断 SQL，只能用于定位候选值、"
                "时间范围、子查询或 JOIN 问题，不能作为最终业务结论。"
                "请基于诊断证据修正原查询，并立即调用 execute_sql_query 执行最终 SQL；"
                "最终 SQL 成功前禁止直接回答用户。"
            )
        if (
            state.requires_fresh_data
            and state.requires_sql_query
            and state.schema_completed
            and state.sql_plan_seen
            and not state.sql_completed
            and not state.ready_to_answer
        ):
            return (
                "【查数顺序要求】你已输出中间推理文本，但尚未执行 execute_sql_query。\n"
                f"{DataQueryPrompts.FORCE_SQL_AFTER_SCHEMA}\n"
                "禁止直接回答用户，必须先完成 SQL 查数。"
            )
        if (
            state.requires_fresh_data
            and state.blocked_content.strip()
            and not state.ready_to_answer
        ):
            if not state.schema_completed:
                return (
                    "【查数顺序要求】本轮新数据查询尚未完成 get_dataset_schema 与 execute_sql_query，"
                    "禁止直接回答用户。\n"
                    f"{DataQueryPrompts.MUST_FETCH_SCHEMA}\n"
                    "请先调用 get_dataset_schema 获取数据集定义，再调用 execute_sql_query 查数。"
                )
            if state.requires_sql_query and not state.sql_completed:
                return (
                    "【查数顺序要求】你已获取数据集 Schema，但尚未执行 execute_sql_query。\n"
                    f"{DataQueryPrompts.FORCE_SQL_AFTER_SCHEMA}\n"
                    "禁止直接回答用户，必须先完成 SQL 查数。"
                )
        return ""

    def _reset_state_for_repair(self, state: _DataRunState) -> None:
        repair_kind = self._current_repair_kind(state)
        state.blocked_content = ""
        state.full_content = ""
        state.content_emitted = False
        state.ignore_text_block = False
        state.active_text_block_id = ""
        state.text_blocks_emitted_since_last_tool = 0
        state.current_text_block_emitted = False
        state.halt_current_react = False
        state.sql_completed = False
        state.sql_error = False
        state.sql_error_message = ""
        state.empty_sql_result = False
        state.empty_sql_reason = ""
        state.sql_plan_missing = False
        state.sql_before_schema = False
        state.sql_static_risk = False
        state.sql_static_risk_reason = ""
        state.failed_sql_repeat_gate_block = False
        if repair_kind in {"empty_sql_result", "ratio_anomaly"}:
            state.expecting_final_sql_after_diagnostic = True
        state.diagnostic_sql_pending_final = False
        state.ratio_anomaly = False          # 比率异常状态清除（每轮重新检测）
        state.ratio_anomaly_reason = ""
        state.duration_anomaly = False
        state.duration_anomaly_reason = ""
        # 彻底清空上一轮的 SQL 缓存，并重置 Schema 未命中状态，防范修复过程中的 Gate 误拦截
        state.last_successful_sql_output = None
        state.successful_sqls = {}
        state.schema_miss = False
        state.schema_needs_refinement = False
        # schema_miss_count 不重置，跨轮累计用于连续未命中达到阈值后终止
        state.tool_loop_detector = ToolLoopDetector()
        state.tool_call_signatures = {}
        if repair_kind != "tool_loop_fuse":
            state.tool_loop_fuse_triggered = False
            state.tool_loop_fuse_reason = ""

    def _resolve_force_execute_sql_tool_choice(self, state: _DataRunState) -> Any | None:
        from agentscope.tool import ToolChoice

        if (
            state.requires_fresh_data
            and state.requires_sql_query
            and state.schema_completed
            and not self._is_schema_fatal(state)
            and not state.sql_completed
        ):
            return ToolChoice(mode="execute_sql_query")
        return None

    def _resolve_initial_tool_choice(self, state: _DataRunState) -> Any | None:
        """Schema 已就绪时，首轮 Agent 第一次模型调用强制 execute_sql_query。"""
        if state.requires_sql_plan and not state.sql_plan_seen:
            return None
        return self._resolve_force_execute_sql_tool_choice(state)

    def _resolve_repair_tool_choice(self, state: _DataRunState) -> Any | None:
        from agentscope.tool import ToolChoice

        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return ToolChoice(mode="get_dataset_schema")
        if state.schema_miss and not state.no_authorized_schema:
            return ToolChoice(mode="get_dataset_schema")
        if state.schema_needs_refinement:
            return ToolChoice(mode="get_dataset_schema")
        if state.schema_ambiguous:
            return None
        if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
            return ToolChoice(mode="get_dataset_schema")
        if state.sql_plan_missing:
            return None
        if state.sql_static_risk:
            return ToolChoice(mode="execute_sql_query")
        if state.failed_sql_repeat_gate_block:
            return ToolChoice(mode="required")
        if state.ratio_anomaly:
            return ToolChoice(mode="required")  # 强制调用工具补充对账 SQL
        if state.duration_anomaly:
            return ToolChoice(mode="execute_sql_query")
        if state.sql_error or state.empty_sql_result or state.diagnostic_sql_pending_final:
            return ToolChoice(mode="required")
        if (
            state.requires_fresh_data
            and state.requires_sql_query
            and state.schema_completed
            and state.sql_plan_seen
            and not state.sql_completed
            and not state.ready_to_answer
        ):
            return self._resolve_force_execute_sql_tool_choice(state)
        if (
            state.requires_fresh_data
            and state.requires_sql_query
            and state.blocked_content.strip()
            and not state.ready_to_answer
        ):
            if not state.schema_completed:
                return ToolChoice(mode="get_dataset_schema")
            return self._resolve_force_execute_sql_tool_choice(state)
        return None

    def _build_repair_title(self, state: _DataRunState) -> str:
        if state.requires_fresh_data and state.sql_before_schema and not state.schema_completed:
            return "必须先检索数据集定义"
        if state.schema_miss and not state.no_authorized_schema:
            return "重试检索数据集定义"
        if state.schema_needs_refinement:
            return "优化数据集定义检索"
        if state.schema_ambiguous:
            return "确认数据集或指标口径"
        if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
            return "必须先重查数据集定义"
        if state.sql_static_risk:
            return "修正高风险 SQL"
        if state.sql_plan_missing:
            return "补充 SQL 计划"
        if state.failed_sql_repeat_gate_block:
            return "修正重复失败 SQL"
        if state.ratio_anomaly:
            return "比率/占比异常复核"
        if state.duration_anomaly:
            return "时间差/时延异常复核"
        if state.tool_loop_fuse_triggered:
            return "停止重复工具调用"
        if state.diagnostic_sql_pending_final:
            return "执行最终 SQL"
        if (
            state.requires_fresh_data
            and state.blocked_content.strip()
            and not state.ready_to_answer
        ):
            if not state.schema_completed:
                return "必须先完成查数流程"
            if state.requires_sql_query and not state.sql_completed:
                return "必须先执行 SQL 查数"
        if (
            state.requires_fresh_data
            and state.requires_sql_query
            and state.schema_completed
            and state.sql_plan_seen
            and not state.sql_completed
            and not state.ready_to_answer
        ):
            return "必须先执行 SQL 查数"
        return "修正 SQL 查询"

    def _has_sql_plan(self, text: str) -> bool:
        if not text:
            return False
        return re.search(r"<sql_plan>\s*\{[\s\S]*?\}\s*</sql_plan>", text, flags=re.IGNORECASE) is not None

    def _should_require_sql_plan(self, user_question: str) -> bool:
        question = (user_question or "").strip().lower()
        if not question:
            return False
        high_risk_keywords = [
            # 中文
            "率", "占比", "比例", "比率", "负载", "利用率", "pue", "成功率", "转化率", "人均", "单价",
            "同比", "环比", "趋势", "变化", "增长", "下降",
            "top", "排名", "排行", "分组", "维度", "group", "join",
            "p95", "p90", "分位", "中位", "median", "percentile",
            # 英文
            "rate", "ratio", "percentage", "percent", "proportion",
            "trend", "growth", "decline", "change", "yoy", "mom",
            "ranking", "rank", "distribution", "utilization", "utilisation",
        ]
        if any(keyword in question for keyword in high_risk_keywords):
            return True
        return re.search(r"按.{0,12}(组|类|类型|维度|机房|区域|部门|用户|状态)", question) is not None

    def _format_sql_result_for_display(self, output: Any, *, max_rows: int = _SQL_RESULT_DISPLAY_MAX_ROWS) -> str:
        parsed = self._try_parse_json_output(output)
        if isinstance(parsed, dict) and self._is_structured_sql_result(parsed):
            display: dict[str, Any] = dict(parsed)
            for key in _SQL_RESULT_ROW_KEYS:
                rows = display.get(key)
                if isinstance(rows, list) and len(rows) > max_rows:
                    display[key] = rows[:max_rows]
                    display["_display_note"] = f"仅展示前 {max_rows} 行，共 {len(rows)} 行"
                    break
            try:
                return json.dumps(display, ensure_ascii=False, indent=2)
            except Exception:
                return str(output or "")
        if isinstance(parsed, list):
            if len(parsed) > max_rows:
                payload = {
                    "_display_note": f"仅展示前 {max_rows} 行，共 {len(parsed)} 行",
                    "rows": parsed[:max_rows],
                }
            else:
                payload = parsed
            try:
                return json.dumps(payload, ensure_ascii=False, indent=2)
            except Exception:
                return str(output or "")
        return str(output or "")

    def _build_sql_error_tool_details(self, output: Any, tool_args: dict[str, Any] | None) -> str:
        text = str(output or "")
        marker = "[Executed SQL]:"
        if marker in text:
            error_part, sql_part = text.split(marker, 1)
            error_part = error_part.strip()
            sql_part = sql_part.strip()
        else:
            error_part = text.strip()
            sql_part = ""
            if tool_args:
                raw_sql = tool_args.get("sql") or tool_args.get("query")
                if isinstance(raw_sql, str) and raw_sql.strip():
                    sql_part = raw_sql.strip()
        error_display = truncate_for_context(error_part, max_len=1000)
        if sql_part:
            return f"[Executed SQL]:\n{sql_part}\n\n{_SQL_TOOL_ERROR_DELIMITER}\n{error_display}"
        return error_display

    def _format_tool_details(
        self,
        tool_name: str,
        output: Any,
        state: _DataRunState,
        tool_args: dict[str, Any] | None = None,
    ) -> str:
        if tool_name == "execute_sql_query" and not self._is_schema_gate_block(output):
            parsed = self._try_parse_json_output(output)
            if self._is_structured_sql_result(parsed):
                result_text = self._format_sql_result_for_display(output)
                result_details = truncate_for_context(result_text, max_len=1000)
                details = result_details
                output_text = str(output or "")
                if "[Executed SQL]:" not in output_text and tool_args:
                    raw_sql = tool_args.get("sql") or tool_args.get("query")
                    if isinstance(raw_sql, str) and raw_sql.strip():
                        details = (
                            f"[Executed SQL]:\n{raw_sql.strip()}\n\n"
                            f"{_SQL_TOOL_RESULT_DELIMITER}\n{result_details}"
                        )
            elif self._is_failed_sql_repeat_gate_block(output):
                details = truncate_for_context(str(output or ""), max_len=1000)
            elif state.sql_error:
                details = self._build_sql_error_tool_details(output, tool_args)
            else:
                result_text = self._format_sql_result_for_display(output)
                details = truncate_for_context(result_text, max_len=1000)
                output_text = str(output or "")
                if "[Executed SQL]:" not in output_text and tool_args:
                    raw_sql = tool_args.get("sql") or tool_args.get("query")
                    if isinstance(raw_sql, str) and raw_sql.strip():
                        details = (
                            f"[Executed SQL]:\n{raw_sql.strip()}\n\n"
                            f"{_SQL_TOOL_RESULT_DELIMITER}\n{details}"
                        )
        else:
            details = truncate_for_context(str(output or ""), max_len=1000)
        if tool_name == "get_dataset_schema":
            from app.services.schema_chunk_format import format_schema_hit_summary

            keywords = (
                self._schema_keywords_from_args(tool_args)
                or state.last_schema_tool_keywords
                or state.last_applied_schema_retry_keywords
                or state.last_schema_keywords
            )
            prefix_lines: list[str] = []
            if keywords:
                prefix_lines.append(f"[检索关键词] {keywords}")
            summary = format_schema_hit_summary(output)
            if summary:
                prefix_lines.append(summary)
            if prefix_lines:
                details = "\n\n".join(prefix_lines) + "\n\n" + details
        if tool_name == "execute_sql_query" and self._is_schema_gate_block(output):
            details = f"{details}\n\n[系统检测] 已拦截：未先获取数据集定义，SQL 未执行。"
        if tool_name == "execute_sql_query" and self._is_failed_sql_repeat_gate_block(output):
            details = f"{details}\n\n[系统检测] 已拦截：禁止原样重复执行已失败的 SQL，请修正后再试。"
        if tool_name == "execute_sql_query" and self._is_sql_repeat_gate_block(output):
            details = f"{details}\n\n[系统检测] 已有成功非空查数结果，已拦截重复 SQL 执行。"
        if tool_name == "execute_sql_query" and self._is_sql_static_gate_block(output):
            details = f"{details}\n\n[系统检测] SQL 存在高风险执行特征，已拦截执行。"
        if tool_name == "execute_sql_query" and self._is_sql_plan_gate_block(output):
            details = f"{details}\n\n[系统检测] 高风险 SQL 缺少结构化 SQL Plan，已拦截执行。"
        if tool_name == "execute_sql_query" and state.empty_sql_reason:
            details = f"{details}\n\n[系统检测] {state.empty_sql_reason}"
        if tool_name == "execute_sql_query" and state.duration_anomaly_reason:
            details = f"{details}\n\n[系统检测] {state.duration_anomaly_reason}"
        if tool_name == "execute_sql_query" and state.sql_error_message:
            error_message = str(state.sql_error_message or "")
            if "[Executed SQL]:" in error_message:
                error_message = error_message.split("[Executed SQL]:", 1)[0].strip()
            details = f"{details}\n\n[系统检测] SQL 执行异常: {error_message[:500]}"
        if tool_name == "get_dataset_schema" and state.schema_miss:
            details = f"{details}\n\n[系统检测] 未命中相关数据集定义。"
        if tool_name == "get_dataset_schema" and state.last_applied_schema_retry_keywords:
            details = (
                f"{details}\n\n[系统检测] 本次重试使用受控重试关键词："
                f"{state.last_applied_schema_retry_keywords}"
            )
        if tool_name == "get_dataset_schema" and state.schema_needs_refinement:
            threshold = self._schema_similarity_threshold or 0.2
            strong = self._schema_strong_confidence_threshold(threshold)
            details = (
                f"{details}\n\n[系统检测] Schema 检索结果相关性不足（最高置信度低于 {strong:.2f}，"
                f"检索阈值 ragflow_similarity_threshold={threshold:.2f}），将换关键词重试。"
            )
        if tool_name == "get_dataset_schema" and state.schema_ambiguous:
            details = f"{details}\n\n[系统检测] Schema 检索结果存在歧义，需要用户确认后再查数。"
        if tool_name == "get_dataset_schema" and state.no_authorized_schema:
            details = f"{details}\n\n[系统检测] 当前用户没有可用授权数据集，已终止查数流程。"
        if tool_name == "get_dataset_schema" and state.schema_service_unavailable:
            details = f"{details}\n\n[系统检测] 元数据服务不可用，已终止查数流程。"
        if tool_name == "get_dataset_schema" and state.rag_not_synced:
            details = f"{details}\n\n[系统检测] 元数据未同步到知识库，已终止查数流程。"
        if state.tool_loop_fuse_triggered and state.tool_loop_fuse_reason:
            details = f"{details}\n\n[系统检测] {state.tool_loop_fuse_reason}"
        return details

    @staticmethod
    def _is_schema_service_unavailable(tool_output: Any) -> bool:
        text = str(tool_output or "")
        if "[元数据服务不可用]" in text:
            return True
        normalized = text.lstrip()
        return normalized.startswith("[Tool Error]") or normalized.startswith("[TOOL_ERROR]")

    def _apply_schema_tool_result(self, state: _DataRunState, output: Any) -> None:
        threshold = self._schema_similarity_threshold or 0.2
        state.schema_service_unavailable = self._is_schema_service_unavailable(output)
        state.no_authorized_schema = self._is_no_authorized_schema(output)
        state.rag_not_synced = self._is_rag_not_synced(output)
        state.schema_miss = self._is_no_relevant_schema(output)
        state.schema_needs_refinement = self._schema_needs_refinement(output, similarity_threshold=threshold)
        state.schema_ambiguous, state.schema_ambiguous_reason = self._detect_schema_ambiguity(output)
        weak_or_miss = state.schema_miss or state.schema_needs_refinement
        if weak_or_miss:
            state.schema_miss_count += 1
        elif not (
            state.schema_service_unavailable
            or state.no_authorized_schema
            or state.rag_not_synced
            or state.schema_ambiguous
            or not str(output or "").strip()
        ):
            state.schema_miss_count = 0
        if (
            self._is_schema_fatal(state)
            or state.schema_miss
            or state.schema_needs_refinement
            or state.schema_ambiguous
            or not str(output or "").strip()
        ):
            state.schema_completed = False
            state.sql_before_schema = False
            state.schema_table_columns = {}
            return
        state.schema_completed = True
        state.sql_before_schema = False
        state.schema_table_columns = self._extract_schema_table_columns(output)
        if state.schema_refresh_required:
            state.schema_refreshed_after_sql_error = True

    def _apply_sql_tool_result(
        self,
        state: _DataRunState,
        *,
        tool_args: dict[str, Any],
        output: Any,
    ) -> tuple[Any, bool]:
        if self._is_sql_repeat_gate_block(output):
            state.sql_repeat_gate_block = True
            state.sql_completed = True
            text = str(output or "")
            cached_text = text
            if "\n\n" in text:
                parts = text.split("\n\n", 1)
                cached_text = parts[1]
            state.last_successful_sql_output = cached_text
            parsed = self._try_parse_json_output(cached_text)
            return parsed, False
        if self._is_sql_static_gate_block(output):
            state.sql_static_risk = True
            state.sql_error = False
            state.sql_error_message = ""
            return output, False
        if self._is_sql_plan_gate_block(output):
            state.sql_plan_missing = True
            state.sql_error = False
            state.sql_error_message = ""
            state.sql_completed = True
            return output, False
        if self._is_failed_sql_repeat_gate_block(output):
            original_error = (
                self._extract_failed_repeat_original_error(output)
                or state.last_sql_error_summary
                or state.sql_error_message
                or ""
            )
            if self._is_failed_sql_repeat_gate_block(original_error):
                original_error = self._extract_failed_repeat_original_error(original_error)
            original_error = str(original_error or "").strip()
            state.failed_sql_repeat_gate_block = True
            state.sql_error = True
            if original_error:
                state.sql_error_message = original_error[:1000]
                state.last_sql_error_summary = original_error[:800]
            state.sql_completed = True
            return output, False
        if self._is_schema_gate_block(output):
            if state.schema_refresh_required and not state.schema_refreshed_after_sql_error:
                state.sql_error = True
                state.sql_error_message = str(output or "")[:1000]
                state.last_sql_error_summary = state.sql_error_message
                state.sql_completed = True
                return output, False
            state.sql_before_schema = True
            return output, False
        if not state.schema_completed:
            state.sql_before_schema = True
            return output, False

        state.sql_completed = True

        parsed_output = self._try_parse_json_output(output)
        empty_reason = self._detect_empty_result(parsed_output) or ""
        sql_text = ""
        if isinstance(tool_args, dict):
            sql_text = str(tool_args.get("sql") or tool_args.get("query") or "")
        is_diag = self._is_diagnostic_sql(sql_text)

        state.sql_error, state.sql_error_message = self._detect_sql_error(output)
        if state.sql_error and self._is_sql_fatal_error(state.sql_error_message):
            state.sql_fatal_error = True
            state.sql_fatal_message = state.sql_error_message
        if state.sql_error:
            normalized_sql = self._normalize_sql_text(sql_text)
            if normalized_sql:
                state.failed_sql_signatures[normalized_sql] = (
                    state.failed_sql_signatures.get(normalized_sql, 0) + 1
                )
                state.last_failed_sql_normalized = normalized_sql
            state.last_sql_error_summary = str(state.sql_error_message or "")[:800]
            if (
                self._is_schema_reference_sql_error(state.sql_error_message)
                and not self._is_sql_schema_preflight_error(output)
            ):
                state.schema_refresh_required = True
                state.schema_refreshed_after_sql_error = False
            return parsed_output, False

        if empty_reason:
            if state.expecting_final_sql_after_diagnostic and not is_diag:
                state.empty_sql_reason = ""
                state.empty_sql_result = False
                state.last_successful_sql_output = output
                state.expecting_final_sql_after_diagnostic = False
                state.diagnostic_sql_pending_final = False
                return parsed_output, True
            if self._is_trusted_empty_result(sql_text, state):
                state.empty_sql_reason = ""
                state.empty_sql_result = False
                state.last_successful_sql_output = output
                state.diagnostic_sql_pending_final = False
            else:
                state.empty_sql_reason = empty_reason
                state.empty_sql_result = True
            return parsed_output, False

        state.empty_sql_reason = ""
        state.empty_sql_result = False
        if is_diag and state.expecting_final_sql_after_diagnostic:
            if (
                state.diagnostic_sql_pending_final
                and self._result_has_data_rows(parsed_output)
            ):
                state.expecting_final_sql_after_diagnostic = False
                state.diagnostic_sql_pending_final = False
            else:
                state.diagnostic_sql_pending_final = True
                return parsed_output, False

        state.expecting_final_sql_after_diagnostic = False
        state.diagnostic_sql_pending_final = False
        state.sql_static_risk = False
        state.sql_static_risk_reason = ""
        state.sql_repeat_gate_block = False
        state.failed_sql_repeat_gate_block = False
        normalized_sql = self._normalize_sql_text(sql_text)
        if normalized_sql:
            state.failed_sql_signatures.pop(normalized_sql, None)
            if state.last_failed_sql_normalized == normalized_sql:
                state.last_failed_sql_normalized = ""
        state.schema_refresh_required = False
        state.schema_refreshed_after_sql_error = False
        state.last_sql_error_summary = ""
        state.last_successful_sql_output = output
        duration_anomaly, duration_reason = self._detect_duration_anomaly(parsed_output)
        if duration_anomaly:
            state.duration_anomaly = True
            state.duration_anomaly_reason = duration_reason
            return parsed_output, False
        state.duration_anomaly = False
        state.duration_anomaly_reason = ""
        ratio_anomaly, anomaly_reason = self._detect_ratio_anomaly(parsed_output)
        if ratio_anomaly:
            state.ratio_anomaly = True
            state.ratio_anomaly_reason = anomaly_reason
        return parsed_output, True

    def _schema_needs_refinement(self, tool_output: Any, *, similarity_threshold: float) -> bool:
        text = str(tool_output or "").strip()
        if not text:
            return False
        if self._is_no_relevant_schema(tool_output):
            return False
        if "Available Datasets (Please provide keywords" in text:
            return True
        confidence_values = self._extract_schema_confidence_values(tool_output)
        if not confidence_values:
            return False
        max_confidence = max(confidence_values)
        strong_threshold = self._schema_strong_confidence_threshold(similarity_threshold)
        if max_confidence < strong_threshold:
            return True
        return False

    @staticmethod
    def _detect_schema_ambiguity(tool_output: Any) -> tuple[bool, str]:
        from app.services.schema_chunk_format import detect_schema_ambiguity
        return detect_schema_ambiguity(tool_output)

    @staticmethod
    def _is_diagnostic_sql(sql: str) -> bool:
        """识别大模型是否在执行用于诊断/对账的临时 SQL"""
        sql_upper = " ".join(str(sql or "").upper().split())
        # 1. 包含 SHOW TABLES / SHOW COLUMNS 等元数据查询
        if any(x in sql_upper for x in ("SHOW TABLES", "SHOW COLUMNS", "DESCRIBE ", "DESC ")):
            return True
        # 2. 包含仅为了查某列唯一值的 DISTINCT 查询
        if "SELECT DISTINCT" in sql_upper and "LIMIT" in sql_upper:
            return True
        # 3. 包含 COUNT 查询且没有 GROUP BY (即只查表行数)
        if "COUNT(" in sql_upper and "GROUP BY" not in sql_upper:
            return True
        return False

    @staticmethod
    def _detect_sql_static_risk(sql: str) -> str:
        sql_text = str(sql or "").strip()
        if not sql_text:
            return "SQL 为空"

        sql_clean = DataAgentRunner._mask_sql_literals_and_comments(sql_text).strip()

        sql_upper = " ".join(sql_clean.upper().split())
        if not sql_upper.startswith(("SELECT ", "WITH ")):
            return "只允许执行只读 SELECT 查询"
        if re.search(r"\bSELECT\s+\*", sql_upper):
            return "SELECT * 会扩大返回范围，请只查询必要字段"

        # 2. 限制 ORDER BY 误杀：排除在 ORDER BY 子句中包含 CASE 的合理情况
        if re.search(r"\bORDER\s+BY\b(?:(?!\bCASE\b)[\s\S]){0,400}\bAND\b[\s\S]{0,120}\b(ROWNUM|LIMIT)\b", sql_upper):
            return (
                "ORDER BY 后不能接 AND ROWNUM/LIMIT；"
                "Oracle TopN 请用子查询包一层排序后外层 ROWNUM，或 FETCH FIRST N ROWS ONLY；"
                "MySQL/ClickHouse 请用 ORDER BY ... LIMIT N"
            )

        # 3. 兼容 CROSS JOIN、USING 以及复杂子查询字数溢出
        if " JOIN " in f" {sql_upper} ":
            if " CROSS JOIN " not in f" {sql_upper} " and " NATURAL JOIN " not in f" {sql_upper} ":
                if not re.search(r"\bJOIN\b[\s\S]{1,400}\b(ON|USING)\b", sql_upper):
                    return "JOIN 缺少明确 ON 或 USING 条件，存在笛卡尔积风险"

        return ""

    @staticmethod
    def _is_rag_not_synced(tool_output: Any) -> bool:
        text = str(tool_output or "")
        return "none are synced to RAG knowledge base" in text

    def _try_parse_json_output(self, tool_output: Any) -> Any:
        if isinstance(tool_output, (list, dict)):
            return tool_output
        if not isinstance(tool_output, str):
            return tool_output
        text = tool_output.strip()
        if not text or text[0] not in "[{":
            return tool_output
        try:
            return json.loads(text)
        except Exception:
            # 限制字符串长度在 5000 以内才使用 ast.literal_eval 兜底，防止解析畸形超大报文时引起 CPU 挂起和 Event Loop 阻塞
            if len(text) < 5000:
                try:
                    return ast.literal_eval(text)
                except Exception:
                    pass
            return tool_output

    def _extract_result_row_lists(self, parsed: Any, depth: int = 0) -> list[list[Any]]:
        if depth > 4:
            return []
        if isinstance(parsed, list):
            return [parsed]
        if not isinstance(parsed, dict):
            return []
        row_lists: list[list[Any]] = []
        for key, value in parsed.items():
            if str(key) not in {"items", "rows", "data", "list", "result", "records"}:
                continue
            if isinstance(value, list):
                row_lists.append(value)
            elif isinstance(value, dict):
                row_lists.extend(self._extract_result_row_lists(value, depth + 1))
        return row_lists

    def _result_has_data_rows(self, parsed: Any) -> bool:
        row_lists = self._extract_result_row_lists(parsed)
        return bool(row_lists and any(len(rows) > 0 for rows in row_lists))

    def _detect_empty_result(self, parsed: Any) -> str | None:
        row_lists = self._extract_result_row_lists(parsed)
        if row_lists and not any(len(rows) > 0 for rows in row_lists):
            return "SQL 返回的行容器为空，未命中任何数据行"
        if isinstance(parsed, dict):
            total_like_keys = ("total", "count", "total_count")
            if (
                any(parsed.get(k) == 0 for k in total_like_keys)
                and row_lists
                and not self._result_has_data_rows(parsed)
            ):
                return "SQL 返回 total/count=0，且未命中任何数据行"
        return None

    def _is_no_authorized_schema(self, tool_output: Any) -> bool:
        text = str(tool_output or "")
        return "No authorized datasets found" in text or "未找到相关的授权数据集" in text

    def _is_no_relevant_schema(self, tool_output: Any) -> bool:
        text = str(tool_output or "")
        return (
            "No relevant schema info found" in text
            or "未找到相关数据集定义" in text
            or "未找到相关的元数据" in text
        )

    def _is_structured_sql_result(self, parsed: Any) -> bool:
        """可解析为 columns/items/rows 等形态时视为正常查数结果，不做关键词误判。"""
        if isinstance(parsed, list):
            return True
        if not isinstance(parsed, dict):
            return False
        if any(key in parsed for key in ("columns", "items", "rows", "data", "records")):
            return True
        return bool(self._extract_result_row_lists(parsed))

    def _detect_sql_error(self, output: Any) -> tuple[bool, str]:
        text = str(output or "")
        if not text.strip():
            return False, ""

        error_prefixes = (
            "[TOOL_ERROR]",
            "[Validation Failed]",
            "[Permission Denied]",
            "[Security Error]",
            f"{SCHEMA_GATE_PREFIX}",
            f"{SQL_PLAN_GATE_PREFIX}",
            "Error: Dataset",
        )
        if any(text.startswith(prefix) for prefix in error_prefixes):
            return True, text[:1000]

        parsed = self._try_parse_json_output(output)
        if self._is_structured_sql_result(parsed):
            return False, ""

        # 剔除可能会误判成功数据集明细中包含“失败/错误”普通文本记录的过宽正则，仅保留系统/数据库底层强报错特征词
        error_patterns = [
            r"unknown column",
            r"unknown table",
            r"syntax error",
            r"sql syntax",
            r"access denied",
            r"permission denied",
            r"unauthorized",
            r"SQL Syntax Error",
            r"SQL Validation Failed",
        ]
        if any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in error_patterns):
            return True, text[:1000]
        return False, ""

    def _is_trusted_empty_result(self, sql: str, state: _DataRunState) -> bool:
        """Allow simple successful empty results to be summarized as "no data"."""
        if state.requires_sql_plan:
            return False
        sql_upper = " ".join(str(sql or "").upper().split())
        if not sql_upper:
            return False
        complex_markers = (
            " JOIN ",
            " GROUP BY ",
            " HAVING ",
            " UNION ",
            " INTERSECT ",
            " EXCEPT ",
            " WITH ",
            " OVER ",
        )
        if any(marker in f" {sql_upper} " for marker in complex_markers):
            return False
        ratio_markers = (
            "RATE",
            "RATIO",
            "PCT",
            "PERCENT",
            "UTILIZATION",
            "利用率",
            "占比",
            "比率",
            "比例",
        )
        if any(marker in sql_upper for marker in ratio_markers):
            return False
        return True

    @staticmethod
    def _is_sql_fatal_error(text: str) -> bool:
        q = str(text or "").strip()
        if not q:
            return False
        fatal_prefixes = (
            "[Permission Denied]",
            "[Security Error]",
            "Error: Dataset",
        )
        if any(q.startswith(prefix) for prefix in fatal_prefixes):
            return True
        fatal_keywords = [
            "未在元数据中注册",
            "拒绝执行",
            "没有表",
            "权限不足",
            "表不存在",
            "table does not exist",
            "access denied",
            "permission denied"
        ]
        q_lower = q.lower()
        return any(kw in q_lower for kw in fatal_keywords)

    @staticmethod
    def _extract_column_names(parsed: dict[str, Any]) -> list[str]:
        columns = parsed.get("columns")
        if not isinstance(columns, list):
            return []
        names: list[str] = []
        for item in columns:
            if isinstance(item, dict):
                name = item.get("name") or item.get("field") or item.get("column")
            else:
                name = item
            if name is None:
                names.append("")
            else:
                names.append(str(name))
        return names

    def _iter_named_result_rows(self, parsed: Any, depth: int = 0) -> list[dict[str, Any]]:
        if depth > 3:
            return []
        if isinstance(parsed, list):
            return [row for row in parsed if isinstance(row, dict)]
        if not isinstance(parsed, dict):
            return []

        rows: list[dict[str, Any]] = []
        column_names = self._extract_column_names(parsed)
        for key in ("items", "rows", "data", "records"):
            value = parsed.get(key)
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict):
                        rows.append(row)
                    elif isinstance(row, list) and column_names:
                        rows.append(
                            {
                                column_names[index]: cell
                                for index, cell in enumerate(row)
                                if index < len(column_names) and column_names[index]
                            }
                        )
                if rows:
                    return rows
            elif isinstance(value, dict):
                rows.extend(self._iter_named_result_rows(value, depth + 1))
                if rows:
                    return rows
        return rows

    @staticmethod
    def _is_duration_like_column(column: str) -> bool:
        name = str(column or "").strip().lower()
        if not name:
            return False
        return bool(
            re.search(
                r"(interval|duration|latency|delay|lag|elapsed|time[_-]?diff|timediff|"
                r"diff[_-]?(seconds|minutes|hours|ms)|age[_-]?(seconds|minutes|hours)|"
                r"seconds|minutes|hours|milliseconds|"
                r"时延|延迟|耗时|时长|时间差|间隔|秒|分钟|小时)",
                name,
                flags=re.IGNORECASE,
            )
        )

    @staticmethod
    def _is_delay_like_column(column: str) -> bool:
        name = str(column or "").strip().lower()
        return bool(re.search(r"(latency|delay|lag|延迟|时延|滞后)", name, flags=re.IGNORECASE))

    def _detect_duration_anomaly(self, parsed: Any) -> tuple[bool, str]:
        """Detect obviously impossible or suspicious duration/time-delta results."""
        rows = self._iter_named_result_rows(parsed)
        if not rows:
            return False, ""

        for row in rows[:50]:
            for column, value in row.items():
                if not self._is_duration_like_column(str(column)):
                    continue
                if isinstance(value, bool):
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                if numeric_value < 0:
                    return True, (
                        f"字段 `{column}` 值为 {numeric_value:g}，时间差/时延/时长不应为负值，"
                        "疑似时间字段相减方向、时区或单位换算错误"
                    )
                if self._is_delay_like_column(str(column)) and numeric_value > DELAY_SECONDS_EXTREME_THRESHOLD:
                    return True, (
                        f"字段 `{column}` 值为 {numeric_value:g} 秒，超过 7 天，"
                        "疑似延迟计算口径、时区或单位换算错误"
                    )
        return False, ""

    @staticmethod
    def _detect_ratio_anomaly(parsed: Any) -> tuple[bool, str]:
        """对 execute_sql_query 结果做比率合理性检测。

        规则：字段名含 rate/ratio/占比/利用率/成功率 等语义词，且值 > 1.5 或 < -0.1，
        则认为可能存在 JOIN 放大或分母口径错误，触发对账修复轮。
        阈值使用 1.5（不是 1.0）以避免合法的"完成率 102%"误触。
        只检查前 50 行，不阻塞大结果集场景。
        """
        ratio_col_pattern = re.compile(
            r"(rate|ratio|pct|percent|proportion|utilization|utilisation|"
            r"\u7387|\u5360\u6bd4|\u6bd4\u4f8b|\u8d1f\u8f7d\u7387|\u5229\u7528\u7387|\u6210\u529f\u7387|\u8f6c\u5316\u7387|\u5b8c\u6210\u7387)",
            re.IGNORECASE,
        )
        rows: list[dict] = []
        if isinstance(parsed, list):
            rows = [r for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            for key in ("items", "rows", "data", "records"):
                val = parsed.get(key)
                if isinstance(val, list):
                    rows = [r for r in val if isinstance(r, dict)]
                    break

        if not rows:
            return False, ""

        for row in rows[:50]:
            for col, val in row.items():
                if not ratio_col_pattern.search(str(col)):
                    continue
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                if fval > 1.5:
                    return True, f"\u5b57\u6bb5 `{col}` \u503c\u4e3a {fval:.4f}\uff08\u8d85\u8fc7 150%\uff0c\u7591\u4f3c JOIN \u653e\u5927\u6216\u5206\u6bcd\u9519\u8bef\uff09"
                if fval < -0.1:
                    return True, f"\u5b57\u6bb5 `{col}` \u503c\u4e3a {fval:.4f}\uff08\u51fa\u73b0\u8d1f\u503c\uff0c\u7591\u4f3c\u8ba1\u7b97\u53e3\u5f84\u9519\u8bef\uff09"
        return False, ""

    async def _resolve_pending_runtime(
        self,
        pending: Any,
    ) -> tuple[Any, list[RuntimeToolSpec], Any, _DataRunState, Dict[str, Any]]:
        if pending.agent is not None and pending.tools and pending.native_model is not None:
            data_state, stream_meta = self._pending_state_to_data_run_state(pending.state or {})
            guarded_tools = self._wrap_tools_with_schema_gate(pending.tools, data_state)
            return pending.agent, guarded_tools, pending.native_model, data_state, stream_meta

        ctx = pending.snapshot.runner_context
        tools = await self._resolve_runtime_tools_from_config()
        native_model_handle = await AgentConfigProvider.get_configured_llm(
            streaming=True,
            config=self.config,
        )
        native_model = getattr(native_model_handle, "native_model", None)
        if native_model is None:
            raise RuntimeError("当前模型适配器未提供 AgentScope native_model，无法恢复挂起执行。")

        from agentscope.state import AgentState

        restored_state = AgentState.model_validate(pending.snapshot.agent_state)
        data_state, stream_meta = self._pending_state_to_data_run_state(
            pending.state or dict(pending.snapshot.stream_state or {})
        )
        guarded_tools = self._wrap_tools_with_schema_gate(tools, data_state)
        agent = await self._build_native_agent(
            native_model=native_model,
            tools=guarded_tools,
            system_content=str(ctx.get("system_content", "")),
            max_steps=int(ctx.get("max_steps", 5)),
            restored_state=restored_state,
            primary_model_name=str(getattr(native_model, "model", self.config.model_name) or ""),
        )
        return agent, guarded_tools, native_model, data_state, stream_meta

    async def _resume_agentscope_native_stream(
        self,
        *,
        pending: Any,
        resume_event: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        agent_name = self._runtime_agent_name()
        try:
            async with agentscope_session_lock.hold(
                user_id=self._runtime_user_id(),
                conversation_id=self.conversation_id,
                agent_name=agent_name,
                ttl_seconds=300,
            ):
                agent, tools, native_model, data_state, stream_meta = await self._resolve_pending_runtime(pending)
                interrupted = False
                async for chunk in self._stream_agentscope_events(
                    event_stream=agent.reply_stream(resume_event),
                    agent=agent,
                    tools=tools,
                    native_model=native_model,
                    state=data_state,
                    stream_meta=stream_meta,
                    emit_final_guard=True,
                ):
                    if is_interrupt_sse_chunk(chunk):
                        interrupted = True
                    yield chunk

                if not interrupted and self.conversation_id:
                    tools_fingerprint = build_tools_fingerprint(self.config, tools)
                    await agent_state_store.save(
                        user_id=self._runtime_user_id(),
                        conversation_id=self.conversation_id,
                        agent_name=agent_name,
                        agent_version=self.config.agent_version,
                        tools_fingerprint=tools_fingerprint,
                        model_name=str(getattr(native_model, "model", self.config.model_name) or ""),
                        state=agent.state,
                    )
        except SessionLockTimeout:
            yield {
                "type": "error",
                "status": "error",
                "content": "当前会话正在处理中，请稍后再试。",
            }

    async def resume_agentscope_native_confirmation(
        self,
        pending: Any,
        *,
        confirmed: bool,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from agentscope.event import ConfirmResult, UserConfirmResultEvent

        event = UserConfirmResultEvent(
            reply_id=pending.reply_id,
            confirm_results=[
                ConfirmResult(
                    confirmed=confirmed,
                    tool_call=pending.tool_call,
                )
            ],
        )
        async for chunk in self._resume_agentscope_native_stream(
            pending=pending,
            resume_event=event,
        ):
            yield chunk

    async def resume_agentscope_external_execution(
        self,
        pending: Any,
        *,
        execution_results: List[Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        from agentscope.event import ExternalExecutionResultEvent

        event = ExternalExecutionResultEvent(
            reply_id=pending.reply_id,
            execution_results=execution_results,
        )
        async for chunk in self._resume_agentscope_native_stream(
            pending=pending,
            resume_event=event,
        ):
            yield chunk
