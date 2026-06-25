"""ChatBI schema prefetch — keyword planning, auto schema invoke, system enrichment."""

from __future__ import annotations

import inspect
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.schemas.agent import AgentExecutionStep
from app.services.ai.config import AgentConfigProvider
from app.services.ai.data_query_semantic_intent import (
    build_semantic_intent_prompt,
    derive_keywords_from_semantic_intent,
    format_semantic_intent_context,
    parse_semantic_intent_payload,
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
from app.services.ai.runtime.agentscope.tools import RuntimeToolSpec

logger = logging.getLogger(__name__)


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
        intent = parse_semantic_intent_payload(content, fallback_question=standalone_query or user_question)
        runner._semantic_intent = intent if intent.has_content() else None
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
        result = schema_spec.callable(keywords=keywords or None)
        if inspect.isawaitable(result):
            result = await result
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
            "get_dataset_schema", output, preview_state, {"keywords": keywords}
        ),
        "status": "error" if runner._is_schema_fatal(preview_state) else "success",
        "category": "tool",
        "execution_time_ms": (time.time() - started_at) * 1000,
    }
    runner._increment_step()
    runner.trace_buffer.append(
        AgentExecutionStep(
            step_number=runner.step_counter,
            event_type="tool_call",
            agent_name=runner.config.agent_name,
            model=runner.config.model_name,
            temperature=float(runner.config.temperature or 0),
            tool_name="get_dataset_schema",
            tool_input={"keywords": keywords},
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
