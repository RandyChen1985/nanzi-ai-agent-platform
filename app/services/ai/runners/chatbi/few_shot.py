"""ChatBI few-shot example search and system prompt injection."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

from app.schemas.agent import AgentExecutionStep

logger = logging.getLogger(__name__)


def _format_search_stats_digest(search_stats: Dict[str, Any], elapsed_ms: float) -> str:
    """把检索过程统计渲染成可读的一行，用于未命中卡片的诊断信息。"""
    mode = search_stats.get("mode", "?")
    mode_label = "local · Redis 向量检索" if mode == "local" else ("ragflow · RAGFlow" if mode == "ragflow" else "?")
    top_k = search_stats.get("top_k")
    threshold = search_stats.get("similarity_threshold")
    recalled = search_stats.get("vector_recalled")
    valid = search_stats.get("valid_sql_after_filter")
    fallback_hits = search_stats.get("mysql_fallback_hits")
    query_raw = search_stats.get("query") or ""
    query_used = search_stats.get("rewritten_query") or query_raw
    mysql_keywords = search_stats.get("mysql_keywords") or []

    parts = [f"检索模式: {mode_label}"]

    # 实际用于检索的词：若经过意图改写，则同时展示改写结果，方便判断"搜错了词"
    if query_used:
        keyword_part = f"检索词: 「{query_used}」"
        if query_raw and query_used != query_raw:
            keyword_part += f"（原问题改写而来）"
        parts.append(keyword_part)

    if top_k is not None:
        parts.append(f"top_k={top_k}")
    if threshold is not None:
        parts.append(f"相似度阈值={threshold:.2f}")
    if recalled is not None:
        parts.append(
            f"向量召回 {recalled} 条 / 阈值后有效 {valid if valid is not None else '?'} 条"
        )
    if fallback_hits is not None:
        kw_text = "、".join(mysql_keywords) if mysql_keywords else query_used or "（无）"
        parts.append(f"关键词兜底 {fallback_hits} 条〔{kw_text}〕")
    parts.append(f"耗时 {max(1, round(elapsed_ms))}ms")

    digest = " · ".join(parts)
    if recalled == 0:
        digest += "\n（向量库中未检索到任何近邻案例，阈值过滤前即为空。）"
    elif valid == 0 and recalled and recalled > 0:
        digest += "\n（已召回近邻，但相似度均低于阈值或案例缺少可用 SQL，未通过过滤。）"
    return digest


def skip_few_shot_log() -> Dict[str, Any]:
    return {
        "type": "log",
        "id": f"fewshot_search_{uuid.uuid4().hex[:8]}",
        "title": "跳过经验库检索",
        "details": "本轮无需新 SQL 生成，已跳过经验库检索以节省延迟。",
        "status": "success",
        "execution_time_ms": 0,
    }


async def inject_few_shot_examples(
    runner: Any,
    system_content: str,
    *,
    user_question: str,
    runtime_messages: List[Any],
) -> str:
    search_start = time.time()
    try:
        from app.services.chatbi_example_service import ExampleService

        search_stats: Dict[str, Any] = {}
        examples = await ExampleService.search_examples(
            user_question,
            dataset_id=None,
            top_k=None,
            history=runtime_messages,
            stats_dump=search_stats,
        )
        runner._fewshot_examples = examples or []
        if not examples:
            elapsed_ms = (time.time() - search_start) * 1000
            runner.trace_buffer.append(
                AgentExecutionStep(
                    step_number=runner._increment_step(),
                    event_type="few_shot",
                    agent_name=runner.config.agent_name,
                    model=str(runner.config.model_name),
                    temperature=float(runner.config.temperature or 0),
                    tool_output={"examples": []},
                    raw_log=f"未命中经验库案例，检索问题：{user_question}",
                    execution_time_ms=elapsed_ms,
                    timestamp=datetime.now(),
                )
            )
            runner._pending_few_shot_log = {
                "type": "log",
                "id": f"fewshot_{uuid.uuid4().hex[:6]}",
                "title": "未命中经验库案例",
                "details": (
                    "已完成经验库检索，但未找到足够相似的历史优质 SQL 案例。\n"
                    f"{_format_search_stats_digest(search_stats, elapsed_ms)}\n"
                    "本轮将继续基于用户问题和数据集定义生成 SQL。"
                ),
                "status": "success",
                "execution_time_ms": elapsed_ms,
            }
            return system_content

        max_sim = max(ex.get("similarity", 0) for ex in examples)
        sim_status = "匹配度极高" if max_sim >= 0.8 else "匹配度一般"
        hit_titles = [
            f"#{ex.get('id', '?')} 「{str(ex.get('question', ''))[:15]}...」 "
            f"(相似度: {ex.get('similarity', 0):.2f})"
            for ex in examples
        ]
        runner.trace_buffer.append(
            AgentExecutionStep(
                step_number=runner._increment_step(),
                event_type="few_shot",
                agent_name=runner.config.agent_name,
                model=str(runner.config.model_name),
                temperature=float(runner.config.temperature or 0),
                tool_output={"examples": examples},
                raw_log="\n".join(hit_titles),
                execution_time_ms=0,
                timestamp=datetime.now(),
            )
        )
        few_shot_block = ExampleService.build_few_shot_prompt(examples)
        if few_shot_block:
            if await runner._using_cache_layout():
                # enabled 灰度桶：few-shot 动态示例追加到稳定前缀之后，避免切断缓存前缀。
                system_content = f"{system_content}\n\n---\n\n{few_shot_block}"
            else:
                # legacy/observe：保留传统前置行为。
                system_content = f"{few_shot_block}\n\n---\n\n{system_content}"
        example_ids = [ex["id"] for ex in examples if ex.get("id")]
        similarities = [ex.get("similarity", 0) for ex in examples if ex.get("id")]
        if example_ids:
            try:
                await ExampleService.record_usage(example_ids, runner.trace_id, similarities=similarities)
            except Exception as ex_rec:
                logger.warning(
                    "[DataAgentRunner] Failed to record few-shot example usage stats: %s",
                    ex_rec,
                )
        runner._pending_few_shot_log = {
            "type": "log",
            "id": f"fewshot_{uuid.uuid4().hex[:6]}",
            "title": f"✨ 命中经验库案例 ({len(examples)}条, {sim_status})",
            "details": (
                "已匹配到历史优质 SQL 案例：\n"
                + "\n".join(hit_titles)
                + f"\n\n当前最高相似度: {max_sim:.2f}。"
                "这些案例将作为强制性参考引导模型生成 SQL，以减少冗余迭代。"
            ),
            "status": "success",
            "execution_time_ms": (time.time() - search_start) * 1000,
        }
        return system_content
    except Exception as e:
        runner._fewshot_examples = []
        logger.warning("[DataAgentRunner] Failed to search/inject few-shot examples: %s", e)
        elapsed_ms = (time.time() - search_start) * 1000
        runner.trace_buffer.append(
            AgentExecutionStep(
                step_number=runner._increment_step(),
                event_type="few_shot",
                agent_name=runner.config.agent_name,
                model=str(runner.config.model_name),
                temperature=float(runner.config.temperature or 0),
                tool_output={"examples": []},
                raw_log=f"经验库检索不可用，已跳过案例注入：{e}",
                execution_time_ms=elapsed_ms,
                status="success",
                timestamp=datetime.now(),
            )
        )
        runner._pending_few_shot_log = {
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
