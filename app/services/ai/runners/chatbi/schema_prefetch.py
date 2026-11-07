"""ChatBI schema prefetch — keyword planning, auto schema invoke, system enrichment."""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep
from app.services.ai.config import AgentConfigProvider
from app.services.ai.temperature import resolve_config_temperature
from app.services.ai.data_query_semantic_intent import (
    build_semantic_intent_prompt,
    derive_keywords_from_semantic_intent,
    format_semantic_intent_context,
    parse_semantic_intent_data,
    parse_semantic_intent_payload,
    try_generate_semantic_intent_structured,
)
from app.services.ai.data_query_turn_classifier import DataQueryTurnType
from app.services.ai.executors.common import extract_tokens_from_message
from app.services.ai.executors.prompts import DataQueryPrompts
from app.services.ai.runtime.agentscope.compat import AIMessage, HumanMessage
from app.services.ai.runners.chatbi.federated_upgrade import (
    extract_schema_dataset_names,
    should_upgrade_to_federated_query,
)
from app.services.ai.runners.chatbi.run_state import DataRunState
from app.services.ai.runners.chatbi import turn_handlers as chatbi_turn_handlers
from app.services.ai.time_anchor import build_data_query_time_anchor_block
from app.services.ai.runtime.agentscope.stream_reconcile import format_tool_args_for_display
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

logger = logging.getLogger(__name__)


def _normalize_scope_token(value: Any) -> str:
    return str(value or "").strip().casefold()


def _collect_session_scope_tokens(execution_options: dict[str, Any] | None) -> set[str]:
    """本轮限定范围的标识集合（``name`` / ``display_name`` / ``id`` 都可能出现）。

    两个来源都读、取并集，而不是择一：

    * :func:`resolve_mounted_dataset_scope_ids` —— 与 ``execute_sql_query`` 的表级
      门禁同源（智能体绑定 > AgentContext > execution_options），判「能不能查」以它为准；
    * ``runner.execution_options`` 里的 ``resource_scope.datasets`` /
      ``metadata_dataset_scope.session`` —— 项目会话手动挂载的旧链路键，有些入口
      只写这里、写不到 AgentContext。

    并集是**保守**方向：宁可多留一个目录块，也不要因为两处范围取值不一致，把用户
    本来能查的数据集从提示词里删掉。
    """
    tokens: set[str] = set()
    try:
        from app.services.ai.metadata_dataset_scope import resolve_mounted_dataset_scope_ids

        for item in resolve_mounted_dataset_scope_ids() or []:
            token = _normalize_scope_token(item)
            if token:
                tokens.add(token)
    except Exception as exc:  # noqa: BLE001 - 范围读不到不能反过来影响目录
        logger.debug("[dataset_menu] 读取运行期范围失败，仅使用执行参数: %s", exc)

    opts = execution_options if isinstance(execution_options, dict) else {}
    scope = opts.get("resource_scope") if isinstance(opts.get("resource_scope"), dict) else {}
    meta_scope = (
        opts.get("metadata_dataset_scope")
        if isinstance(opts.get("metadata_dataset_scope"), dict)
        else {}
    )
    raw_items = list(scope.get("datasets") or []) + list(meta_scope.get("session") or [])
    for item in raw_items:
        if isinstance(item, dict):
            for key in ("dataset_name", "name", "display_name", "id"):
                token = _normalize_scope_token(item.get(key))
                if token:
                    tokens.add(token)
        elif isinstance(item, (str, int)):
            token = _normalize_scope_token(item)
            if token:
                tokens.add(token)
    tokens.discard("")
    return tokens


async def expand_scope_tokens_with_names(tokens: set[str]) -> set[str]:
    """把纯数字 id 补成数据集 name。

    运行期范围（``ctx.metadata_dataset_ids`` / 智能体绑定）存的是 **id**，而目录文本里
    只有 ``- Dataset: <name> [tags]``，不比对这一步就永远匹配不上——裁剪会静默失效
    （或更糟，按「一个都没匹配到」把目录清空）。只在确实含数字 id 时查一次库。
    """
    numeric = {token for token in tokens if token.isdigit()}
    if not numeric:
        return tokens
    try:
        from sqlalchemy import select

        from app.core.orm import AsyncSessionLocal
        from app.models.metadata import MetaDataset

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(MetaDataset.id, MetaDataset.name).where(
                        MetaDataset.id.in_(sorted({int(token) for token in numeric}))
                    )
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - 解析失败只是少一层收窄，不能阻断
        logger.warning("[dataset_menu] 把范围 id 解析成名称失败: %s", exc)
        return tokens
    expanded = set(tokens)
    for row in rows:
        name = _normalize_scope_token(row[1])
        if name:
            expanded.add(name)
    return expanded


def apply_session_resource_scope(
    dataset_menu: str,
    execution_options: dict[str, Any] | None = None,
    *,
    tokens: set[str] | None = None,
) -> str:
    """项目会话有手动挂载数据集时，只把挂载的数据集注入本轮上下文。

    匹配不上时**保留原目录**（fail-open）。范围标识与目录里 ``- Dataset:`` 用名
    不同源（传的是 id 或别名）是常态，据此清空目录会让模型直接答「你没有数据集」，
    比「看得见但执行时被拦」更糟；多留几个块最多是被门禁拦下，模型仍能据目录
    解释范围。真正的「能不能查」由 ``execute_sql_query`` 判定，不靠提示词自觉。

    ``tokens`` 用于传入**已经解析过**的范围标识（例如把 id 换成 name 后的结果），
    给了就不再从 ``execution_options`` 里重新收集。
    """
    menu_text = str(dataset_menu or "")
    mounted = tokens if tokens is not None else _collect_session_scope_tokens(execution_options)
    if not mounted:
        return menu_text
    blocks = re.split(r"(?=^- Dataset:)", menu_text, flags=re.MULTILINE)
    header_blocks: list[str] = []
    matched_blocks: list[str] = []
    for block in blocks:
        first_line = block.splitlines()[0] if block.splitlines() else ""
        if not first_line.lstrip().startswith("- Dataset:"):
            header_blocks.append(block)
            continue
        raw_after_colon = first_line.split(":", 1)[1].strip().casefold()
        name_match = re.search(r"^- Dataset:\s*([^\s\[]+)", first_line)
        pure_name = name_match.group(1).strip().casefold() if name_match else raw_after_colon
        display_match = re.search(r"^\s*Display Name:\s*(.+)$", block, flags=re.MULTILINE)
        display_name = display_match.group(1).strip().casefold() if display_match else ""
        if (
            pure_name in mounted
            or raw_after_colon in mounted
            or (display_name and display_name in mounted)
        ):
            matched_blocks.append(block)
    if matched_blocks:
        updated_headers = [
            re.sub(
                r"(【当前用户有权访问的数据集】共\s*)\d+(\s*个)",
                rf"\g<1>{len(matched_blocks)}\g<2>",
                h,
            )
            for h in header_blocks
        ]
        return "".join(updated_headers + matched_blocks)
    logger.warning(
        "[dataset_menu] 会话范围 %s 没匹配到任何目录条目（目录共 %s 个），按原样保留；"
        "范围标识与目录名可能不同源（id / 别名）",
        sorted(mounted)[:5],
        menu_text.count("- Dataset:"),
    )
    return menu_text


# 兼容旧引用：模块内与既有测试按私有名导入，改名不破坏调用点
_apply_session_resource_scope = apply_session_resource_scope


def user_is_admin(user_info: Any) -> bool:
    """统一的「是否管理员」判定。

    同一个 ``user_info`` 在两处被读成两种结果：目录链路看 ``role == "admin"``，
    澄清链路看 ``is_admin`` 字段。字段缺失时会出现「目录按管理员生成、权限段按普通
    用户生成」的分裂，所以收敛成一个判定。
    """
    info = user_info if isinstance(user_info, dict) else {}
    if bool(info.get("is_admin")):
        return True
    return str(info.get("role") or "").strip().lower() == "admin"


def _dataset_names_in_menu(menu_text: str) -> set[str]:
    """目录里实际出现的数据集标识（小写），用于过滤权限段。"""
    return {
        match.group(1).strip().casefold()
        for match in re.finditer(r"^- Dataset:\s*([^\s\[]+)", str(menu_text or ""), flags=re.MULTILINE)
    }


_ROW_STATE_LABELS = {
    "custom": "行级过滤（自定义条件）",
    "filtered": "行级过滤（按角色策略）",
    "deny_all": "行级条件导致实际查不到数据",
}


def _format_permission_row(entry: dict[str, Any], permission: dict[str, Any]) -> str:
    label = str(entry.get("display_name") or entry.get("name") or entry.get("id") or "?")
    parts: list[str] = []

    tables = permission.get("tables") or {}
    if tables.get("mode") == "list":
        names = [str(name) for name in (tables.get("names") or []) if str(name).strip()]
        shown = "、".join(names) if names else "详见权限页"
        parts.append(f"只能访问 {tables.get('visible', 0)}/{tables.get('total', 0)} 张表：{shown}")

    columns = permission.get("columns") or {}
    column_details = [
        f"{item.get('table') or item.get('table_id')}: {'/'.join(item.get('names') or [])}"
        for item in (columns.get("tables") or [])
        if item.get("names")
    ]
    if column_details:
        parts.append("字段白名单 " + "；".join(column_details))
    elif columns.get("restricted_tables"):
        parts.append(f"{columns.get('restricted_tables')} 张表的字段受限（列名不公开）")

    row_state = str(permission.get("row") or "")
    if row_state in _ROW_STATE_LABELS:
        parts.append(_ROW_STATE_LABELS[row_state])

    if permission.get("expires_at"):
        parts.append(f"{permission['expires_at']} 到期")

    return f"- {label}：{'；'.join(parts)}" if parts else f"- {label}：无收窄"


async def build_dataset_permission_block(
    user_id: Any,
    is_admin: bool,
    *,
    visible_names: set[str] | None = None,
    max_items: int = 8,
) -> str:
    """目录尾部的**实时**权限段：只列存在收窄的数据集。

    为什么不写进目录本体、跟着 90 天缓存一起存：权限比数据集变更频繁得多，写进缓存
    等于把过期权限固化三个月——模型会照着旧范围承诺查询。所以目录仍走缓存，这一段
    每次请求现算（与 ``list_accessible_dataset_entries`` / 目录工具同一口径）。

    ``visible_names`` 用于对齐「本轮挂载裁剪后」的目录：被裁掉的数据集不该在权限段里
    继续出现，否则模型会以为它还可用。
    """
    if is_admin or not user_id:
        return ""
    try:
        from app.core.orm import AsyncSessionLocal
        from app.services.metadata_service import MetadataService

        async with AsyncSessionLocal() as session:
            entries = await MetadataService.list_accessible_dataset_entries(
                session,
                user_id=int(user_id),
                is_admin=False,
                status=1,
            )
    except Exception as exc:  # noqa: BLE001 - 权限段是增强信息，失败不能阻断目录
        logger.warning("[dataset_menu] 生成实时权限范围失败: %s", exc)
        return ""

    rows: list[str] = []
    for entry in entries or ():
        permission = entry.get("permission") or {}
        if not permission:
            continue
        name = str(entry.get("name") or "").strip().casefold()
        if visible_names is not None and name not in visible_names:
            continue
        rows.append(_format_permission_row(entry, permission))
    if not rows:
        return ""

    lines = ["", "本轮数据集权限范围（实时）：", *rows[:max_items]]
    if len(rows) > max_items:
        lines.append(f"（另有 {len(rows) - max_items} 个数据集有收窄，未列出。）")
    lines.append("（未列出的数据集没有授权收窄，可访问其中全部表与字段。）")
    return "\n".join(lines) + "\n"


def compact_dataset_menu_in_system_content(system_content: str) -> str:
    """预取 Schema 成功后，剥离 system_content 中 dataset_menu 的 Table Details 明细行以减少 ReAct 循环 Token。"""
    if not system_content or "  Table Details:\n" not in system_content:
        return system_content
    compacted = re.sub(
        r"^  Table Details:\n(?:    - [^\n]*\n?)+",
        "",
        system_content,
        flags=re.MULTILINE,
    )
    compacted = compacted.replace(
        "Includes Tables=表术语，Table Details=表描述，Metrics=指标名。",
        "Includes Tables=表术语，Metrics=指标名。",
    )
    return compacted


@dataclass
class SchemaSetupOutcome:
    system_content: str
    prefetched_schema_output: Optional[str] = None
    stop_execution: bool = False


def example_schema_keyword_context(examples: List[Dict[str, Any]], limit: int = 3) -> str:
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
                    "GROUP", "ORDER", "LIMIT", "SUM", "COUNT", "AVG", "MAX", "MIN",
                    "AND", "OR", "ON", "BY", "AS", "DESC", "ASC", "WITH", "CASE",
                    "WHEN", "THEN", "ELSE", "END",
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


def clean_schema_fallback_query(text: str) -> str:
    stop_words = [
        "分析", "统计", "查询", "获取", "列出", "展示", "显示", "查一下",
        "情况", "关于", "的", "在", "内", "后",
    ]
    cleaned = text
    for word in stop_words:
        cleaned = cleaned.replace(word, " ")
    return " ".join(cleaned.split())


def semantic_intent_recent_context(runtime_messages: List[Any]) -> str:
    lines: list[str] = []
    for message in list(runtime_messages or [])[-7:-1]:
        if not isinstance(message, (HumanMessage, AIMessage)):
            continue
        content = getattr(message, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        content = re.sub(r"\s+", " ", content).strip()
        if not content:
            continue
        role = "用户" if isinstance(message, HumanMessage) else "助手"
        lines.append(f"{role}: {content[:220]}")
    return "\n".join(lines)


def format_need_analysis_success_details(runner: Any, keywords: str) -> str:
    details = f"已完成用户需求分析，并生成问题关键词。\n问题关键词: {keywords}"
    semantic_intent_context = format_semantic_intent_context(runner._semantic_intent)
    if semantic_intent_context:
        details = f"{details}\n\n{semantic_intent_context}"
    return details


def is_invalid_schema_search_keywords(keywords: str) -> bool:
    normalized = re.sub(r"\s+", "", str(keywords or "")).strip().lower()
    if not normalized:
        return True
    return normalized in {
        "...", "…", "keyword", "keywords", "关键词", "问题关键词",
        "n/a", "na", "none", "null", "无",
    }


async def plan_schema_search_keywords(
    runner: Any,
    user_question: str,
    standalone_query: str,
    examples: List[Dict[str, Any]],
    runtime_messages: List[Any] | None = None,
) -> str:
    try:
        user_id = getattr(runner.config, "user_id", None)
        is_admin = getattr(runner.config, "is_admin", False)
        dataset_menu = await AgentConfigProvider.get_dataset_menu(user_id=user_id, is_admin=is_admin)
        dataset_menu = _apply_session_resource_scope(dataset_menu, getattr(runner, "execution_options", None))
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to fetch dataset menu for schema prefetch: %s", e)
        dataset_menu = ""

    fallback_query = clean_schema_fallback_query((standalone_query or user_question or "").strip())[:300]
    prompt = build_semantic_intent_prompt(
        user_question=user_question,
        standalone_query=standalone_query,
        example_context=example_schema_keyword_context(examples),
        conversation_context=semantic_intent_recent_context(runtime_messages or []),
        available_datasets=dataset_menu,
    )
    try:
        model = await AgentConfigProvider.get_configured_llm(streaming=False, config=runner.config)
        fallback_question = standalone_query or user_question
        intent = None
        used_structured = False
        structured_payload, structured_response = await try_generate_semantic_intent_structured(
            getattr(model, "native_model", None),
            prompt,
        )
        if structured_payload is not None:
            candidate = parse_semantic_intent_data(
                structured_payload,
                fallback_question=fallback_question,
            )
            if candidate and candidate.has_content():
                intent = candidate
                used_structured = True
                usage = getattr(structured_response, "usage", None)
                prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
                completion_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
                if prompt_tokens or completion_tokens:
                    runner.record_llm_token_usage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        event_type="thought",
                        model=str(getattr(model, "model_name", runner.config.model_name) or ""),
                        tool_name="schema_keyword_planner",
                    )
                logger.info(
                    "[DataAgentRunner] schema keyword planner used AgentScope structured_output"
                )
        if not used_structured:
            response = await model.ainvoke([HumanMessage(content=prompt)])
            tokens = extract_tokens_from_message(response)
            runner.record_llm_token_usage(
                prompt_tokens=tokens["prompt_tokens"],
                completion_tokens=tokens["completion_tokens"],
                event_type="thought",
                model=str(getattr(model, "model_name", runner.config.model_name) or ""),
                tool_name="schema_keyword_planner",
            )
            content = (getattr(response, "content", "") or "").strip()
            intent = parse_semantic_intent_payload(
                content,
                fallback_question=fallback_question,
            )

        runner._semantic_intent = intent if intent and intent.has_content() else None
        keywords = (intent.keywords if intent else "").strip()
        if is_invalid_schema_search_keywords(keywords):
            derived_keywords = derive_keywords_from_semantic_intent(runner._semantic_intent)
            if derived_keywords and not is_invalid_schema_search_keywords(derived_keywords):
                if runner._semantic_intent:
                    runner._semantic_intent.keywords = derived_keywords
                return derived_keywords[:300]
            if runner._semantic_intent:
                runner._semantic_intent.keywords = fallback_query
            return fallback_query
        return keywords[:300]
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to plan schema search keywords: %s", e)
        runner._semantic_intent = None
        return fallback_query


async def auto_invoke_get_dataset_schema(
    runner: Any,
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
        from app.core.context import get_current_agent_context

        ctx = get_current_agent_context()
        metadata_dataset_ids = [
            str(dataset_id).strip()
            for dataset_id in (getattr(ctx, "metadata_dataset_ids", None) or [])
            if str(dataset_id).strip()
        ]
        invoke_kwargs: dict[str, Any] = {"keywords": keywords or None}
        if metadata_dataset_ids:
            invoke_kwargs["metadata_dataset_ids"] = metadata_dataset_ids
        # 预取仍然是一次真实工具调用，必须经过 RuntimeToolSpec 的统一最终
        # 收口，才能生成 ToolResultEnvelope；不要直接绕过到 callable。
        result = await schema_spec.invoke(invoke_kwargs, call_id=tool_id)
        output = str(result or "")
    except Exception as exc:
        logger.error("[DataAgentRunner] Auto get_dataset_schema failed: %s", exc)
        output = f"[TOOL_ERROR] 自动获取数据集定义失败: {exc}"
    preview_state = DataRunState()
    preview_state.last_schema_tool_keywords = applied_keywords
    runner._apply_schema_tool_result(preview_state, output)
    yield {
        "type": "log",
        "id": tool_id,
        "title": "工具完成: get_dataset_schema",
        "details": runner._format_tool_details(
            "get_dataset_schema", output, preview_state, invoke_kwargs
        ),
        "status": "error" if runner._is_schema_fatal(preview_state) else "success",
        "category": "tool",
        "execution_time_ms": (time.time() - started_at) * 1000,
        "tool_args": format_tool_args_for_display(
            invoke_kwargs, tool_name="get_dataset_schema"
        ),
    }
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="tool_call",
            agent_name=runner.config.agent_name,
            model=runner.config.model_name,
            temperature=resolve_config_temperature(runner.config),
            tool_name="get_dataset_schema",
            tool_input=invoke_kwargs,
            tool_output=output,
            raw_log=output[:4000],
            execution_time_ms=(time.time() - started_at) * 1000,
            timestamp=datetime.fromtimestamp(started_at),
        )
    )
    yield {"__schema_output__": output}


def should_rewrite_contextual_new_data_query(user_question: str, runtime_messages: List[Any]) -> bool:
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
        "本月呢", "上月呢", "今天呢", "昨天呢", "本周呢", "上周呢", "再按", "再看", "再查",
        "换成", "改成", "只看", "只查", "也看", "也查", "then", "this", "that", "it",
        "previous", "last one", "what about", "还是", "刚才的", "之前那个", "不是", "用这个",
        "数据查询", "数据需求", "查数据", "查数", "数据智能体",
    ]
    if any(marker in q_lower for marker in context_markers):
        return True
    query_verbs = ["查询", "查", "统计", "列出", "展示", "显示", "获取", "select", "show", "list"]
    return len(q) < 12 and not any(verb in q_lower for verb in query_verbs)


async def resolve_standalone_query_for_new_data_query(
    runner: Any,
    user_question: str,
    runtime_messages: List[Any],
) -> str:
    q = (user_question or "").strip()
    system_prompt = getattr(runner.config, "system_prompt", None)
    if not isinstance(system_prompt, str):
        system_prompt = ""
    ltm_match = re.search(r"(\[Memory Profile\][\s\S]*?)(?=\n\n\[|\Z)", system_prompt)
    ltm_context = ltm_match.group(1).strip() if ltm_match else ""
    has_ltm = bool(ltm_context.replace("[Memory Profile]", "").strip()) if ltm_context else False
    need_rewrite = has_ltm or should_rewrite_contextual_new_data_query(q, runtime_messages)
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
        "你是 ChatBI 查询改写器。请根据最近对话和用户偏好，把【最新提问】改写成一句独立、完整、适合检索元数据和历史 SQL 案例的查数问题。\n要求：\n"
        + "\n".join(instructions)
        + ltm_section
        + f"\n\n{time_anchor}"
    )
    if recent_history:
        prompt += "\n\n【最近对话】\n" + "\n".join(recent_history)
    prompt += f"\n\n【最新提问】\n{q}\n\n【改写后的独立查数问题】"
    try:
        model = await AgentConfigProvider.get_configured_llm(streaming=False, config=runner.config)
        response = await model.ainvoke([HumanMessage(content=prompt)])
        tokens = extract_tokens_from_message(response)
        runner.record_llm_token_usage(
            prompt_tokens=tokens["prompt_tokens"],
            completion_tokens=tokens["completion_tokens"],
            event_type="thought",
            model=str(getattr(model, "model_name", runner.config.model_name) or ""),
            tool_name="standalone_query_rewrite",
        )
        rewritten = (getattr(response, "content", "") or "").strip().strip('"').strip("'")
        if not rewritten:
            return q
        return rewritten[:300]
    except Exception as e:
        logger.warning("[DataAgentRunner] Failed to rewrite standalone data query: %s", e)
        return q


async def yield_need_analysis_phase(
    runner: Any,
    *,
    user_question: str,
    runtime_messages: List[Any],
    system_content: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    need_analysis_start = time.time()
    need_analysis_log_id = f"need_analysis_{uuid.uuid4().hex[:8]}"
    yield {
        "type": "log",
        "id": need_analysis_log_id,
        "title": "用户需求分析",
        "details": (
            "正在结合用户原始问题与经验库案例，生成用于元数据检索的问题关键词..."
            if runner._fewshot_examples
            else "正在分析用户原始问题，生成用于元数据检索的问题关键词..."
        ),
        "status": "pending",
        "started_at": int(need_analysis_start * 1000),
    }
    runner._schema_search_keywords = await plan_schema_search_keywords(
        runner,
        user_question,
        runner._standalone_query,
        runner._fewshot_examples,
        runtime_messages=runtime_messages,
    )
    updated_content = system_content + (
        "\n\n【Schema 检索词规划】本轮已结合"
        + ("用户原始问题和经验库案例" if runner._fewshot_examples else "用户原始问题")
        + f"规划出 get_dataset_schema 的检索词：{runner._schema_search_keywords}\n"
        "首次检索数据集定义时，请优先使用这些 keywords；这些词仅用于检索元数据，不代表最终 SQL 表字段已确认。\n"
        f"【独立查数问题】{runner._standalone_query}"
    )
    semantic_intent_context = format_semantic_intent_context(runner._semantic_intent)
    if semantic_intent_context:
        updated_content += f"\n\n{semantic_intent_context}"
    yield {
        "type": "log",
        "id": need_analysis_log_id,
        "title": "用户需求分析",
        "details": format_need_analysis_success_details(
            runner,
            runner._schema_search_keywords or runner._standalone_query or user_question,
        ),
        "status": "success",
        "execution_time_ms": (time.time() - need_analysis_start) * 1000,
    }
    yield {"__system_content__": updated_content}


async def yield_fresh_data_schema_setup(
    runner: Any,
    outcome: SchemaSetupOutcome,
    *,
    turn_cls: Any,
    user_question: str,
    runtime_messages: List[Any],
    tools: list[RuntimeToolSpec],
) -> AsyncGenerator[Dict[str, Any], None]:
    """Plan keywords, prefetch schema, enrich system prompt; may stop for federated/fatal."""
    if turn_cls.requires_fresh_data and turn_cls.requires_few_shot:
        async for chunk in yield_need_analysis_phase(
            runner,
            user_question=user_question,
            runtime_messages=runtime_messages,
            system_content=outcome.system_content,
        ):
            if chunk.get("__system_content__") is not None:
                outcome.system_content = str(chunk["__system_content__"])
                continue
            yield chunk

    if not turn_cls.requires_fresh_data:
        return

    await runner._ensure_schema_similarity_threshold()
    schema_keywords = (
        runner._schema_search_keywords or runner._standalone_query or user_question or ""
    ).strip()
    prefetched_schema_output: str | None = None
    async for chunk in auto_invoke_get_dataset_schema(runner, keywords=schema_keywords, tools=tools):
        if chunk.get("__schema_output__") is not None:
            prefetched_schema_output = str(chunk["__schema_output__"])
            continue
        yield chunk

    outcome.prefetched_schema_output = prefetched_schema_output
    runner._authorized_schema_output = prefetched_schema_output
    if not prefetched_schema_output:
        return

    from app.services.ai.runners.chatbi.schema_prefetch_fatal import (
        build_prefetch_fatal_probe_state,
        is_prefetch_schema_fatal,
    )

    if is_prefetch_schema_fatal(runner, prefetched_schema_output):
        fatal_state = build_prefetch_fatal_probe_state(runner, prefetched_schema_output)
        async for chunk in runner._yield_schema_fatal_abort(fatal_state, prefetched_schema_output):
            yield chunk
        outcome.stop_execution = True
        return

    outcome.system_content += "\n\n" + DataQueryPrompts.prefetched_schema_context(
        schema_keywords, prefetched_schema_output
    )
    schema_binding_summary = runner._build_schema_binding_summary(prefetched_schema_output)
    if schema_binding_summary:
        outcome.system_content += f"\n\n{schema_binding_summary}"

    if turn_cls.turn_type == DataQueryTurnType.METADATA_QUERY:
        async for chunk in chatbi_turn_handlers.dispatch_metadata_schema_turn(
            runner,
            runtime_messages=runtime_messages,
            system_content=outcome.system_content,
            user_question=user_question,
            prefetched_schema_output=prefetched_schema_output,
        ):
            yield chunk
        outcome.stop_execution = True
        return

    outcome.system_content = compact_dataset_menu_in_system_content(outcome.system_content)

    datasets = sorted(extract_schema_dataset_names(prefetched_schema_output))
    classified_as_federated = (
        turn_cls.turn_type == DataQueryTurnType.FEDERATED_DATA_QUERY and len(datasets) > 1
    )
    should_federate = should_upgrade_to_federated_query(
        prefetched_schema_output,
        runner._standalone_query,
    )
    if len(datasets) > 1 and not classified_as_federated and not should_federate:
        yield {
            "type": "log",
            "id": f"multi_ds_hint_{uuid.uuid4().hex[:8]}",
            "title": "检测到多个数据集",
            "details": (
                f"本次 Schema 检索命中 {len(datasets)} 个数据集：{', '.join(datasets)}。\n"
                "若您需要跨数据集关联分析，可在问题中明确「关联/对比/联合/跨库」等表述，"
                "或点击快捷提问：\n"
                "- [🔗 升级为跨数据集联邦查询](quick:请对刚才命中的多个数据集做跨源关联查询)"
            ),
            "status": "warning",
        }
    if classified_as_federated or should_federate:
        async for chunk in chatbi_turn_handlers.run_federated_prefetch_upgrade(
            runner,
            turn_cls=turn_cls,
            prefetched_schema_output=prefetched_schema_output,
            system_content=outcome.system_content,
            runtime_messages=runtime_messages,
        ):
            yield chunk
        outcome.stop_execution = True
