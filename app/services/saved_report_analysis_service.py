"""黄金报表执行后业务解读（会话与订阅共用结构化结论）。"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from app.services.saved_report_digest_service import (
    MAX_AI_SOURCE_ROWS,
    _parse_ai_digest,
    enrich_digest_with_ai,
)

logger = logging.getLogger(__name__)

SESSION_ANALYSIS_SYSTEM = """你是数据分析助手，只基于输入中的报表数据做中文业务解读。

硬性约束：
1. 必须优先回应 original_query / report_title 中的分析意图（对比、趋势、排名、异常等）。
2. 只能使用 records 中出现的数值与字段；禁止编造同比、环比、目标、预算、行业标准、政策或未给出的业务背景。
3. 若有多行时间/维度数据，关键结论与详细分析必须包含可核对的对比（如最高/最低、相邻期变化、差额或变化幅度）；数据不足以对比时写明「当前结果无法判断」。
4. risk_note 只能描述 records 中可见的异常或数据缺口（如断崖下降、空值、样本过少）；没有可见异常时 risk_note 必须为空字符串。
5. 字段名优先使用 column_labels；没有标签时用简洁业务表述，不要堆砌英文技术标识。
6. 输出纯 JSON：{"key_findings":["2至4条，每条80字内"],"analysis":["3至5条，每条100字内"],"risk_note":"可选，100字内或空字符串"}。
7. 不要输出 Markdown、quick 链接或推荐按钮。"""


def render_analysis_markdown(
    *,
    report_title: str,
    analysis_payload: Mapping[str, Any],
) -> str:
    findings = [str(item).strip() for item in (analysis_payload.get("key_findings") or []) if str(item).strip()]
    details = [str(item).strip() for item in (analysis_payload.get("analysis") or []) if str(item).strip()]
    risk = str(analysis_payload.get("risk_note") or "").strip()
    sections = [f"### 📌 业务解读（黄金报表「{report_title}」）"]
    if findings:
        sections.append("**关键结论**")
        sections.extend(f"- {item}" for item in findings)
    if details:
        sections.append("**详细分析**")
        sections.extend(f"- {item}" for item in details)
    if risk:
        sections.append("**关注事项**")
        sections.append(f"- {risk}")
    if len(sections) == 1:
        sections.append("- 暂无可展示的解读结论。")
    return "\n\n".join(sections)


def _preview_records(
    parsed_result: Any,
    *,
    column_labels: Optional[Mapping[str, str]] = None,
    limit: int = MAX_AI_SOURCE_ROWS,
) -> List[Dict[str, Any]]:
    labels = column_labels or {}
    rows: List[Any] = []
    if isinstance(parsed_result, dict):
        for key in ("rows", "items", "data", "records", "result"):
            candidate = parsed_result.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break
        # result_snapshot 形态
        if not rows and isinstance(parsed_result.get("rows"), list):
            rows = parsed_result["rows"]
    elif isinstance(parsed_result, list):
        rows = parsed_result

    preview: List[Dict[str, Any]] = []
    for raw in rows[:limit]:
        if isinstance(raw, dict):
            labeled: Dict[str, Any] = {}
            for key, value in list(raw.items())[:8]:
                name = str(key)
                label = str(labels.get(name) or labels.get(name.lower()) or name)
                labeled[label] = value
            preview.append(labeled)
        elif isinstance(raw, (list, tuple)):
            preview.append({f"列{i + 1}": value for i, value in enumerate(list(raw)[:8])})
        else:
            preview.append({"值": raw})
    return preview


async def analyze_saved_report_result(
    *,
    report_title: str,
    original_query: Optional[str],
    parsed_result: Any,
    column_labels: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    analysis_instruction: Optional[str] = None,
    generator: Optional[Callable[[str], Awaitable[str]]] = None,
) -> Dict[str, Any]:
    """
    返回：
    {
      analysis: {key_findings, analysis, risk_note} | None,
      analysis_markdown: str | None,
      analysis_status: success|fallback|disabled|error,
      analysis_snapshot: dict,
    }
    """
    query = (original_query or "").strip() or None
    digest_seed = {
        "title": report_title,
        "scope": "会话执行" if not analysis_instruction else "按分析偏好",
        "key_findings": [],
        "records": _preview_records(parsed_result, column_labels=column_labels),
        "analysis": [],
        "risk_note": None,
        "generation_mode": "fallback",
        "ai_status": "disabled",
        "original_query": query,
        "analysis_goal": query or report_title,
        "column_labels": dict(column_labels or {}),
        "params": dict(params or {}),
        "grounding_rules": [
            "只依据 records 中的数值",
            "必须回应 original_query/analysis_goal",
            "禁止编造目标、预算、同比环比口径外推",
            "risk_note 仅描述可见异常，否则为空",
        ],
    }

    async def _session_generator(prompt: str) -> str:
        if generator:
            return await generator(prompt)
        from app.core.llm.client import get_llm_async
        from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
        from app.services.ai.runtime.agentscope.messages import RuntimeContentBlock, RuntimeMessage

        llm = await get_llm_async(streaming=False, temperature=0.1, source="report.analysis")
        if llm is None:
            raise RuntimeError("LLM unavailable")
        try:
            payload = json.loads(prompt)
        except Exception:
            payload = {"raw_prompt": prompt}
        enriched_prompt = json.dumps(
            {
                **payload,
                "system_hint": SESSION_ANALYSIS_SYSTEM,
            },
            ensure_ascii=False,
        )
        messages = [
            RuntimeMessage(role="system", content=[RuntimeContentBlock(type="text", text=SESSION_ANALYSIS_SYSTEM)]),
            RuntimeMessage(role="user", content=[RuntimeContentBlock(type="text", text=enriched_prompt)]),
        ]
        return await chat_client_from_handle(llm).generate_text(messages, temperature=0.1)

    try:
        enriched = await enrich_digest_with_ai(
            digest_seed,
            enabled=True,
            analysis_instruction=analysis_instruction,
            generator=_session_generator,
        )
    except Exception as exc:
        logger.warning("Saved report session analysis failed: %s", type(exc).__name__)
        return {
            "analysis": None,
            "analysis_markdown": None,
            "analysis_status": "error",
            "analysis_snapshot": {
                "key_findings": [],
                "analysis": [],
                "risk_note": None,
                "generation_mode": "fallback",
                "ai_status": "error",
            },
        }

    status = str(enriched.get("ai_status") or "fallback")
    if status != "success":
        return {
            "analysis": None,
            "analysis_markdown": None,
            "analysis_status": status,
            "analysis_snapshot": {
                "key_findings": list(enriched.get("key_findings") or []),
                "analysis": list(enriched.get("analysis") or []),
                "risk_note": enriched.get("risk_note"),
                "generation_mode": enriched.get("generation_mode") or "fallback",
                "ai_status": status,
            },
        }

    analysis = {
        "key_findings": list(enriched.get("key_findings") or []),
        "analysis": list(enriched.get("analysis") or []),
        "risk_note": enriched.get("risk_note"),
    }
    try:
        _parse_ai_digest(json.dumps(analysis, ensure_ascii=False))
    except Exception:
        return {
            "analysis": None,
            "analysis_markdown": None,
            "analysis_status": "fallback",
            "analysis_snapshot": {
                **analysis,
                "generation_mode": "fallback",
                "ai_status": "fallback",
            },
        }

    markdown = render_analysis_markdown(report_title=report_title, analysis_payload=analysis)
    snapshot = {
        **analysis,
        "generation_mode": "ai",
        "ai_status": "success",
        "analysis_markdown": markdown,
    }
    return {
        "analysis": analysis,
        "analysis_markdown": markdown,
        "analysis_status": "success",
        "analysis_snapshot": snapshot,
    }
