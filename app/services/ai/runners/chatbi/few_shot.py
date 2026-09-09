"""ChatBI few-shot example search and system prompt injection."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List

from app.schemas.agent import AgentExecutionStep

logger = logging.getLogger(__name__)


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

        examples = await ExampleService.search_examples(
            user_question,
            dataset_id=None,
            top_k=None,
            history=runtime_messages,
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
