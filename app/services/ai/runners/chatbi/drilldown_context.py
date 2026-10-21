"""Prompt contract for condition-inheriting conversational drill-downs."""

from __future__ import annotations

import json
from typing import Any, Mapping


def build_inherited_analysis_context(
    previous_result: Mapping[str, Any] | None,
    user_question: str,
) -> str:
    """Render semantic conditions only; never encourage copying stale SQL."""
    result = dict(previous_result or {})
    context = result.get("analysis_context")
    if not isinstance(context, Mapping):
        context = {}
    raw_filters = context.get("filters") or []
    filters = dict(raw_filters) if isinstance(raw_filters, Mapping) else list(raw_filters)
    inherited = {
        "previous_question": str(
            result.get("question") or result.get("user_question") or ""
        ).strip(),
        "dataset_name": str(result.get("dataset_name") or "").strip(),
        "metrics": list(context.get("metrics") or []),
        "dimensions": list(context.get("dimensions") or []),
        "filters": filters,
        "time_range": context.get("time_range"),
        "time_grain": context.get("time_grain"),
        "analysis_method": context.get("analysis_method"),
    }
    return (
        "\n\n【对话式下钻：继承上一结果的分析条件】\n"
        "本轮是在上一查询基础上继续分析。默认保留未被用户明确修改的业务对象、指标、"
        "筛选条件和时间范围；只切换用户本轮指定的维度、时间粒度或分析方法。\n"
        f"上一分析语义：{json.dumps(inherited, ensure_ascii=False, default=str)}\n"
        f"本轮变化请求：{str(user_question or '').strip()}\n"
        "必须重新基于当前 Schema 生成 SQL，不得直接复制上一轮 SQL；若新要求与继承条件冲突，"
        "以本轮明确要求为准，并在答案中说明发生了什么变化。"
    )
