"""ChatBI/DataQueryExecutor 专用轮次分类。

该模块只服务数据查询执行器。路由层只负责选择智能体；一旦进入
DataQueryExecutor，本模块负责判断本轮是新数据查询、复用上一轮结果，还是上下文动作。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from app.services.ai.intent_service import (
    IntentResponse,
    IntentType,
    looks_like_compound_query_with_viz,
    looks_like_context_action,
    looks_like_pure_result_followup,
    looks_like_skill_execution,
)
from app.services.ai.runtime.agentscope.chat import chat_client_from_handle
from app.services.ai.runtime.agentscope.messages import system_user_prompt_messages
from app.services.ai.turn_classifier import (
    load_last_data_result,
)

logger = logging.getLogger(__name__)


class DataQueryTurnType(str, Enum):
    NEW_DATA_QUERY = "new_data_query"
    DATA_FOLLOWUP_QUERY = "data_followup_query"
    METADATA_QUERY = "metadata_query"
    REUSE_PREVIOUS_RESULT = "reuse_previous_result"
    RESULT_ANALYSIS = "result_analysis"
    RESULT_PRESENTATION = "result_presentation"
    RESULT_ACTION = "result_action"
    CONTEXT_ACTION = "context_action"
    SKILL_EXECUTION = "skill_execution"
    CLARIFICATION_OR_NON_DATA = "clarification_or_non_data"
    NON_DATA_REQUEST = "non_data_request"
    CLARIFICATION_REQUIRED = "clarification_required"
    FORMAT_CORRECTION = "format_correction"
    FEDERATED_DATA_QUERY = "federated_data_query"


DATA_QUERY_TURN_TYPE_LABELS: dict[DataQueryTurnType, str] = {
    DataQueryTurnType.NEW_DATA_QUERY: "新数据查询",
    DataQueryTurnType.DATA_FOLLOWUP_QUERY: "带上下文的数据追问",
    DataQueryTurnType.METADATA_QUERY: "元数据探索",
    DataQueryTurnType.REUSE_PREVIOUS_RESULT: "复用上一轮结果",
    DataQueryTurnType.RESULT_ANALYSIS: "结果分析",
    DataQueryTurnType.RESULT_PRESENTATION: "结果呈现调整",
    DataQueryTurnType.RESULT_ACTION: "结果动作",
    DataQueryTurnType.CONTEXT_ACTION: "上下文动作",
    DataQueryTurnType.SKILL_EXECUTION: "技能执行",
    DataQueryTurnType.CLARIFICATION_OR_NON_DATA: "需澄清或非查数请求",
    DataQueryTurnType.NON_DATA_REQUEST: "非查数请求",
    DataQueryTurnType.CLARIFICATION_REQUIRED: "查数需求待澄清",
    DataQueryTurnType.FORMAT_CORRECTION: "样式与图表微调",
    DataQueryTurnType.FEDERATED_DATA_QUERY: "跨数据集联邦查询",
}



def data_query_turn_type_label(turn_type: DataQueryTurnType) -> str:
    return DATA_QUERY_TURN_TYPE_LABELS.get(turn_type, turn_type.value)


@dataclass
class DataQueryTurnClassification:
    turn_type: DataQueryTurnType
    reasoning: str
    requires_fresh_data: bool = True
    requires_few_shot: bool = True
    requires_sql_query: bool = True
    skip_intent_llm: bool = False
    intent: Optional[IntentType] = None
    missing_fields: tuple[str, ...] = ()


def _classification_for_turn_type(
    turn_type: DataQueryTurnType,
    reasoning: str,
    *,
    skip_intent_llm: bool,
) -> DataQueryTurnClassification:
    if turn_type in {
        DataQueryTurnType.REUSE_PREVIOUS_RESULT,
        DataQueryTurnType.RESULT_ANALYSIS,
    }:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type in {
        DataQueryTurnType.FORMAT_CORRECTION,
        DataQueryTurnType.RESULT_PRESENTATION,
    }:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type == DataQueryTurnType.FEDERATED_DATA_QUERY:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=True,
            requires_few_shot=True,
            requires_sql_query=True,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type == DataQueryTurnType.METADATA_QUERY:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=True,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type in {
        DataQueryTurnType.CONTEXT_ACTION,
        DataQueryTurnType.RESULT_ACTION,
    }:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type == DataQueryTurnType.SKILL_EXECUTION:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type in {
        DataQueryTurnType.CLARIFICATION_OR_NON_DATA,
        DataQueryTurnType.NON_DATA_REQUEST,
        DataQueryTurnType.CLARIFICATION_REQUIRED,
    }:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.GENERAL,
        )
    return DataQueryTurnClassification(
        turn_type=turn_type if turn_type == DataQueryTurnType.DATA_FOLLOWUP_QUERY else DataQueryTurnType.NEW_DATA_QUERY,
        reasoning=reasoning,
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=skip_intent_llm,
        intent=IntentType.DATA_QUERY,
    )


def _is_unattended_automation_query(user_query: str) -> bool:
    """TaskCenter 无人值守任务：无人可回答澄清，禁止整轮拦截。"""
    q = str(user_query or "")
    return "【自动化指令" in q or "TaskCenter 自动任务" in q


def _looks_like_explicit_new_data_query(user_query: str) -> bool:
    """高置信的新查数请求，避免占用后续 Schema/SQL 编排 LLM 调用。"""
    q = (user_query or "").strip().lower()
    if not q:
        return False
    keywords = [
        "查询", "查一下", "查下", "统计", "列出", "列表", "筛选", "过滤",
        "最近", "今天", "昨天", "本周", "上周", "本月", "上月", "获取",
        "拉取", "明细", "记录", "趋势", "报表", "汇总", "对比", "多少",
        "用户表", "用户列表", "query", "how many", "count", "select ",
        "from ", "where ", "group by", "table", "users",
        "查看", "关联", "调用情况", "访问日志",
        "巡检", "动环", "显示一下", "显示", "最后时间", "间隔时间",
    ]
    return any(keyword in q for keyword in keywords) or _looks_like_business_status_data_query(q)


# 软性缺口：可由执行阶段合理默认补齐，不应单独触发整轮澄清拦截
_SOFT_CLARIFICATION_FIELDS = frozenset({"time_range", "metric", "dimension"})
# 真正硬性：缺则无法安全复用结果或无法在多口径间选择
_STRICT_HARD_CLARIFICATION_FIELDS = frozenset({"result_context", "dataset_or_schema"})
# 含 data_object：仅在问题中已有具体业务对象时可降级继续
_HARD_CLARIFICATION_FIELDS = frozenset({
    "data_object", "result_context", "dataset_or_schema",
})

_CONCRETE_OBJECT_SIGNALS = (
    "表", "数据集", "字段", "日志", "明细", "记录", "智能体", "代理",
    "用户", "订单", "机房", "门店", "客户", "项目", "渠道", "工单",
    "数据中心", "动环", "指标", "节点", "网关", "采集",
)

_METRIC_SATISFIED_SIGNALS = (
    "指标", "多少", "数量", "统计", "汇总", "趋势", "占比", "排名", "对比",
    "消耗", "用量", "延迟", "延时", "最后时间", "间隔", "最新时间", "更新时间",
    "采集时间", "上报时间", "pue", "count", "sum", "avg", "max", "min",
)

_TIME_SATISFIED_SIGNALS = (
    "今天", "昨天", "本周", "上周", "本月", "上月", "今年", "去年",
    "最近", "近期", "天内", "月内", "季度", "年度", "最后时间", "间隔时间",
    "与现在", "当前时间", "实时",
)

_DIMENSION_SATISFIED_SIGNALS = (
    "各", "每", "按", "分组", "维度", "分机房", "分区域", "分部门", "groupby", "group by",
)


def _has_concrete_data_object(user_query: str) -> bool:
    """问题中是否已点名可执行的业务对象（用于纠正 LLM 误报 data_object）。"""
    q = (user_query or "").strip().lower()
    if not q:
        return False
    return any(signal in q for signal in _CONCRETE_OBJECT_SIGNALS)


def _query_satisfies_clarification_field(user_query: str, field: str) -> bool:
    """原问题是否已覆盖某类澄清缺口（用于剔除 LLM 误报）。"""
    q = (user_query or "").strip().lower()
    if not q or not field:
        return False
    if field == "data_object":
        return _has_concrete_data_object(q)
    if field == "metric":
        return any(signal in q for signal in _METRIC_SATISFIED_SIGNALS)
    if field == "time_range":
        if any(signal in q for signal in _TIME_SATISFIED_SIGNALS):
            return True
        return bool(re.search(r"20\d{2}[-/\.年]", q))
    if field == "dimension":
        return any(signal in q for signal in _DIMENSION_SATISFIED_SIGNALS)
    # result_context / dataset_or_schema：无法仅凭当前句子判定已满足
    return False


def filter_satisfied_missing_fields(
    user_query: str,
    missing_fields: tuple[str, ...] | None,
) -> tuple[str, ...]:
    """剔除原问题中已满足的缺口，避免建议补已经写明的条件。"""
    if not missing_fields:
        return ()
    return tuple(
        field
        for field in missing_fields
        if not _query_satisfies_clarification_field(user_query, field)
    )


def _looks_like_actionable_data_query(user_query: str) -> bool:
    """对象/动作已足够明确，可用默认时间与口径继续查数。"""
    q = (user_query or "").strip().lower()
    if not q:
        return False
    if _looks_like_explicit_new_data_query(q) or _looks_like_business_status_data_query(q):
        return True
    action_signals = (
        "查看", "查", "统计", "列出", "关联", "join", "分析", "汇总",
        "对比", "筛选", "获取", "拉取", "显示", "巡检",
    )
    has_object = _has_concrete_data_object(q)
    has_action = any(signal in q for signal in action_signals)
    return has_object and has_action and len(q) >= 8


def _should_proceed_despite_clarification(
    user_query: str,
    missing_fields: tuple[str, ...],
) -> bool:
    """软性缺口或可纠正的 data_object 误报在对象已明确时不拦截。

    无人值守自动化任务无人可答澄清，一律放行继续查数。
    调用前应先用 filter_satisfied_missing_fields 剔除已满足缺口。
    """
    if _is_unattended_automation_query(user_query):
        return True
    fields = set(missing_fields or ())
    if not fields:
        return True
    # 结果上下文 / 多口径冲突：继续查数容易答非所问，保持拦截
    if fields & _STRICT_HARD_CLARIFICATION_FIELDS:
        return False
    # 仅软性缺口，或软性 + 误报的 data_object（问题里已有具体对象）
    if not fields <= (_SOFT_CLARIFICATION_FIELDS | {"data_object"}):
        return False
    if "data_object" in fields and not _has_concrete_data_object(user_query):
        return False
    return _looks_like_actionable_data_query(user_query)


def _looks_like_business_status_data_query(user_query: str) -> bool:
    """识别“查看某业务对象的状态/延迟/异常”等不一定包含“查询”的查数请求。"""
    q = (user_query or "").strip().lower()
    if not q:
        return False

    action_signals = [
        "我要看", "想看", "看一下", "看看", "查看", "检查", "确认", "检测",
        "监控", "展示", "显示", "排查",
    ]
    status_or_metric_signals = [
        "是否延迟", "延迟", "延时", "滞后", "是否正常", "状态", "运行情况",
        "运行状态", "同步情况", "采集时间", "更新时间", "上报时间", "最新",
        "离线", "在线", "异常", "失败", "成功", "断流", "超时", "积压",
        "缺失", "波动", "偏高", "偏低",
    ]

    action_matches = [(q.find(signal), signal) for signal in action_signals if signal in q]
    status_matches = [(q.find(signal), signal) for signal in status_or_metric_signals if signal in q]
    if not action_matches or not status_matches:
        return False

    action_idx, action = min(action_matches, key=lambda item: item[0])
    status_idx, _ = min(status_matches, key=lambda item: item[0])
    if status_idx <= action_idx:
        return False

    object_text = q[action_idx + len(action):status_idx]
    object_text = re.sub(r"[，,。？！\s]|的|一下|一下子|是否|是不是", "", object_text)
    vague_refs = {"这个", "那个", "这些", "那些", "它", "其", "上面", "刚才"}
    if not object_text or object_text in vague_refs:
        return False

    scope_markers = ["各", "每", "所有", "全部", "全量", "不同", "各个", "各类", "各项"]
    return len(object_text) >= 2 or any(marker in q for marker in scope_markers)


def _classification_for_clarification(
    reasoning: str,
    *,
    skip_intent_llm: bool,
    missing_fields: tuple[str, ...] = ("result_context",),
) -> DataQueryTurnClassification:
    result = _classification_for_turn_type(
        DataQueryTurnType.CLARIFICATION_REQUIRED,
        reasoning,
        skip_intent_llm=skip_intent_llm,
    )
    result.intent = IntentType.DATA_QUERY
    result.missing_fields = missing_fields
    return result


def _classification_for_non_data(reasoning: str, *, skip_intent_llm: bool) -> DataQueryTurnClassification:
    return _classification_for_turn_type(
        DataQueryTurnType.NON_DATA_REQUEST,
        reasoning,
        skip_intent_llm=skip_intent_llm,
    )


def _assistant_content_shows_data_result(content: str) -> bool:
    """识别助手回复中是否已展示可查数/可追问的数据结果（含 Markdown 表格与图表）。"""
    text = str(content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    data_context_signals = [
        "上一轮", "刚才", "刚刚", "已返回", "查询结果", "结构化查询结果",
        "数据结果", "表格", "图表", "sql", "rows", "items",
        "列表", "统计结果", "可视化", "分析解读", "数据集",
        "```chart", "| ---", "|---",
    ]
    if any(signal in lowered for signal in data_context_signals):
        return True
    if re.search(r"^\s*\|.+\|", text, flags=re.MULTILINE):
        pipe_lines = [
            line for line in text.splitlines()
            if line.strip().startswith("|") and line.count("|") >= 2
        ]
        if len(pipe_lines) >= 2:
            return True
    if re.search(r"\d+(\.\d+)?%", text) and re.search(r"(万元|万|亿|同比|环比|趋势|回款|收入|占比)", text):
        return True
    return False


def history_shows_recent_data_result(messages: Optional[List[Dict[str, str]]]) -> bool:
    """最近对话中是否存在可追问的数据展示（不依赖 Redis 缓存）。"""
    if not messages:
        return False
    for msg in reversed(messages[:-1] or messages):
        if msg.get("role") != "assistant":
            continue
        if _assistant_content_shows_data_result(str(msg.get("content") or "")):
            return True
    return False


def _has_reuse_context(*, has_last_data_result: bool, messages: Optional[List[Dict[str, str]]]) -> bool:
    return has_last_data_result or history_shows_recent_data_result(messages)


def looks_like_result_action(user_query: str, *, has_last_data_result: bool) -> bool:
    """Return whether the request operates on the current result instead of querying data."""
    if not has_last_data_result:
        return False
    q = (user_query or "").strip().lower()
    if not q or _looks_like_explicit_new_data_query(q):
        return False
    result_reference = any(
        signal in q
        for signal in ("结果", "刚才", "刚刚", "上轮", "上一轮", "这个数据", "该数据", "这些数据")
    )
    action = any(
        signal in q
        for signal in (
            "导出", "下载", "保存", "发送", "发给", "分享",
            "订阅", "监控", "告警", "提醒", "简报", "汇报材料", "汇报稿",
        )
    )
    return result_reference and action


def _looks_like_general_chat_or_unsupported(
    user_query: str,
    *,
    has_last_data_result: bool,
    messages: Optional[List[Dict[str, str]]] = None,
) -> bool:
    q = (user_query or "").strip().lower()
    if not q:
        return False
    has_reuse_context = _has_reuse_context(
        has_last_data_result=has_last_data_result,
        messages=messages,
    )
    if looks_like_context_action(q) or looks_like_skill_execution(q):
        return False
    if looks_like_compound_query_with_viz(q) or _looks_like_explicit_new_data_query(q):
        return False
    if has_reuse_context and looks_like_pure_result_followup(q):
        return False
    if has_reuse_context and any(
        signal in q
        for signal in (
            "这个数据", "该数据", "这些数据", "这个结果", "该结果",
            "上轮", "上一轮", "刚才", "刚刚", "分析", "解读", "可视化", "图表", "总结",
        )
    ):
        return False

    general_signals = [
        "你好", "您好", "你是谁", "你能做什么", "谢谢", "感谢", "辛苦了",
        "什么模型", "哪个模型", "模型版本", "写一封", "写邮件", "写文章",
        "翻译一下", "帮我翻译", "润色一下", "改写一下",
    ]
    if any(signal in q for signal in general_signals) or re.search(r"\b(hi|hello)\b", q):
        return True

    vague_refs = ["这个", "那个", "这份", "这张", "上面", "刚才", "刚刚"]
    vague_actions = ["看看", "看一下", "分析", "解读", "处理", "帮我"]
    return (
        not has_reuse_context
        and any(ref in q for ref in vague_refs)
        and any(action in q for action in vague_actions)
    )


def _recent_history_supports_reuse(messages: Optional[List[Dict[str, str]]]) -> bool:
    """Avoid silently reusing stale data after the conversation has drifted away."""
    if not messages:
        return True
    recent = messages[-7:-1]
    if len(recent) < 3:
        return True

    for msg in recent:
        if msg.get("role") != "assistant":
            continue
        if _assistant_content_shows_data_result(str(msg.get("content") or "")):
            return True
    return False


def _can_reuse_previous_result(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    has_last_data_result: bool,
    allow_contextual_llm_reuse: bool = False,
) -> bool:
    # 新业务对象、时间范围或明确查数动作优先于“可视化/分析”动作，
    # 否则同一句“本周各智能体调用情况并可视化”会被旧结果短路。
    if _looks_like_explicit_new_data_query(user_query):
        return False
    if not looks_like_pure_result_followup(user_query):
        return False
    q = (user_query or "").strip().lower()
    contextual_only_signals = ("你好", "您好", "hi", "hello", "这列", "那列", "该列")
    if not allow_contextual_llm_reuse and any(signal in q for signal in contextual_only_signals):
        return False
    if not _recent_history_supports_reuse(messages):
        return False
    return has_last_data_result or history_shows_recent_data_result(messages)


def _recent_history_for_prompt(messages: Optional[List[Dict[str, str]]]) -> str:
    if not messages:
        return "无"
    lines = []
    for msg in messages[-6:]:
        role = msg.get("role") or ""
        if role not in ("user", "assistant"):
            continue
        content = re.sub(r"\s+", " ", str(msg.get("content") or "")).strip()
        if not content:
            continue
        if len(content) > 240:
            content = content[:240] + "..."
        role_name = "用户" if role == "user" else "助手"
        lines.append(f"{role_name}: {content}")
    return "\n".join(lines) if lines else "无"


def _parse_llm_json(content: str) -> Dict:
    text = (content or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group())
        except Exception:
            return {}


async def _classify_with_llm(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    has_last_data_result: bool,
) -> Optional[DataQueryTurnClassification]:
    prompt = f"""你是 ChatBI 数据查询执行器内部的请求类别分析器。

请结合最近对话、当前用户问题，以及是否存在上一轮结构化查询结果，判断当前请求属于以下哪一类：

1. new_data_query：新的独立业务数据查询，不依赖上一轮结果条件。
1a. data_followup_query：需要重新查库，但继承上一轮结果的业务对象或条件，并切换/增加维度、时间粒度、筛选范围或分析方法。
2. metadata_query：对当前有权访问的数据集结构、表结构、可查询字段、分析口径、元数据信息的探索提问（例如“说明智能体有哪些字段”、“支持查询哪些指标”、“列名有啥”）。
3. reuse_previous_result：兼容旧分类；不需要重新查库的结果复用请求。
3a. result_analysis：不重新查库，基于上一轮结构化结果解释变化、归因、总结、比较或提炼结论。
3b. result_presentation：不重新查库，只调整图表类型、样式、表格格式或展示方式。
4. context_action：兼容旧分类；对已有上下文执行动作。
4a. result_action：对上一轮结果执行保存、导出、发送、生成简报、订阅或沉淀等动作。
5. skill_execution：显式要求使用/执行某个技能。
6. non_data_request：身份、模型、能力、闲聊、写作、翻译、通用知识等非查数请求。
7. clarification_required：已确认想查业务数据，但缺少**真正阻塞执行**的信息。仅限：完全说不清查什么对象；要基于上一轮结果继续但没有可复用结果；多个数据集/字段口径冲突无法安全选择。
8. format_correction：不需要重新查库，只是对图表的展现形式或样式进行微调（如将折线图改为柱状图、给特定数据标红、添加图表参考线等样式调整）。
9. federated_data_query：用户显式要求跨数据集/跨库/跨源/联邦/联合查询，或明确要求关联多个数据集、数据源、库或表。

约束：
- 默认优先继续查数：能合理默认时间（如近期→最近30天）、统计方式或维度时，必须选 new_data_query，不要 clarification_required。
- 不要因为未写精确日期、未出现“指标口径/分析维度”等 BI 术语，或“近期/最近”较模糊就澄清。
- 用户已给出表/对象/字段/关联关系，或问题已足够可执行时，必须选 new_data_query。
  例：「查看智能体主表中的名称和引擎类型，并关联访问日志统计近期调用」→ new_data_query。
  例：「按各数据中心显示动环指标最后时间并计算与现在的间隔」→ new_data_query（对象与指标已明确）。
- 带「【自动化指令」或「TaskCenter 自动任务」前缀的无人值守任务：禁止 clarification_required，必须选 new_data_query（或明确的 data_followup_query / federated_data_query）；无人可回答澄清。
- 如果选择 reuse_previous_result、result_analysis、result_presentation、result_action 或 format_correction，必须确认“存在上一轮结构化查询结果”为 true。
- 如果用户基于上一轮对象或条件切换维度、时间粒度、筛选范围或分析方法，选择 data_followup_query；完全独立的新查询才选 new_data_query。
- 如果用户明确要求跨数据集、跨库、联邦查询，或要求关联多个数据集/数据源/库/表，选择 federated_data_query。
- 如果用户只是提问元数据字段/分析口径，选择 metadata_query。
- clarification_required 必须返回至少一个 missing_fields，可选值：data_object、metric、time_range、dimension、result_context、dataset_or_schema。
  仅在硬性阻塞时使用；软性缺口（仅缺时间/指标/维度且对象已明确）不要选 clarification_required。
- 只返回 JSON，不要解释，不要 Markdown。

JSON 格式：
{{"turn_type":"new_data_query|data_followup_query|metadata_query|reuse_previous_result|result_analysis|result_presentation|context_action|result_action|skill_execution|non_data_request|clarification_required|format_correction|federated_data_query","reasoning":"一句中文原因","missing_fields":[]}}

【存在上一轮结构化查询结果】
{str(has_last_data_result).lower()}

【最近对话】
{_recent_history_for_prompt(messages)}

【当前用户问题】
{user_query}
"""
    try:
        from app.services.ai.config import AgentConfigProvider

        llm = await AgentConfigProvider.get_configured_llm(streaming=False)
        chat_client = chat_client_from_handle(llm)
        content = await chat_client.generate_text(
            system_user_prompt_messages(prompt, user_prompt=user_query)
        )
        data = _parse_llm_json(content)
        raw_turn_type = str(data.get("turn_type") or "").strip()
        reasoning = str(data.get("reasoning") or "ChatBI 请求类别由大模型兜底识别").strip()
        turn_type = DataQueryTurnType(raw_turn_type)
        result = _classification_for_turn_type(turn_type, reasoning, skip_intent_llm=False)
        allowed_missing_fields = {
            "data_object", "metric", "time_range", "dimension",
            "result_context", "dataset_or_schema",
        }
        raw_missing_fields = data.get("missing_fields")
        if isinstance(raw_missing_fields, list):
            result.missing_fields = tuple(
                field for field in raw_missing_fields
                if isinstance(field, str) and field in allowed_missing_fields
            )
        if turn_type == DataQueryTurnType.CLARIFICATION_REQUIRED:
            if not result.missing_fields:
                return _classification_for_non_data(
                    "未确认到可结构化描述的查数缺口，按非查数请求引导切换智能体",
                    skip_intent_llm=False,
                )
            result.intent = IntentType.DATA_QUERY
        return result
    except Exception as e:
        logger.warning("[DataQueryTurnClassifier] LLM fallback classification failed: %s", e)
        return None


def _looks_like_explicit_federated_query(user_question: str) -> bool:
    """检测用户问题是否包含明确的跨数据集/联邦查询意图。

    用于 LLM 分类结果的兜底校验：LLM 误把单数据集查询归类为联邦查询成本很高
    （额外一次计划生成 LLM 调用），此函数通过明确关键词过滤降低误升级率。
    """
    q = (user_question or "").strip().lower()
    if not q:
        return False
    explicit_terms = (
        "跨数据集", "跨库", "跨源", "联邦查询", "多数据集",
        "多个数据集", "不同数据集", "不同库", "联合查询",
    )
    if any(term in q for term in explicit_terms):
        return True
    # 弱启发式：「关联」+「数据集/数据源」同现时才升级，避免误判单数据集 JOIN 或"数据库连接"等无关语境。
    has_join_action = any(term in q for term in ("关联", "join", "联结"))
    has_multi_source_hint = any(term in q for term in ("数据集", "数据源"))
    return has_join_action and has_multi_source_hint


def looks_like_metadata_query(q: str) -> bool:
    """识别诸如 '说明智能体数据集里有哪些可查询字段和适合的分析口径'、'字段说明'、'有什么字段' 等元数据/分析口径的探索提问，
    这些提问虽然不直接查询业务数据，但应该直接交给 AI 结合提示词中的数据集 Schema 进行回答，不应拦截为澄清。
    """
    q_clean = q.lower().strip()
    metadata_keywords = [
        "字段说明", "有什么字段", "有哪些字段", "可查询字段", 
        "字段定义", "分析口径", "字段列表", "口径说明",
        "业务主题", "覆盖哪些业务", "有哪些业务", "哪些指标",
        "可用指标", "能分析哪些", "可以分析哪些", "适合做什么分析",
    ]
    return any(kw in q_clean for kw in metadata_keywords)


def looks_like_chart_format_correction(q: str) -> bool:
    """识别样式/图表类型微调，如“改为折线图”、“饼图换成柱状图”、“柱子标红”等。"""
    q_clean = q.lower().strip()
    fresh_query_patterns = [
        r"(重新|再|重新再)?(查询|查一下|查下|查|统计|筛选|过滤|拉取)",
        r"按.{1,12}(分组|维度|地区|区域|部门|用户|销售|客户)",
        r"(本月|上月|本周|上周|今天|昨天|最近).{0,12}(数据|记录|明细|统计)",
    ]
    if any(re.search(pat, q_clean) for pat in fresh_query_patterns):
        return False
    patterns = [
        r"改(成|为)(折线|柱状|饼|环形|面积|条形|雷达|散点|漏斗|热力|图)",
        r"(折线|柱状|饼|环形|面积|条形|雷达|散点|漏斗|热力|图)改(成|为)",
        r"图表改(成|为)",
        r"换成(折线|柱状|饼|环形|面积|条形|雷达|散点|漏斗|热力|图)",
        r"标红",
        r"颜色改为",
        r"字体大小",
        r"图例",
        r"显示数值",
        r"隐藏数值"
    ]
    return any(re.search(pat, q_clean) for pat in patterns)


def looks_like_result_presentation(q: str) -> bool:
    """识别仅改变结果展示、不改变查询条件的请求。"""
    q_clean = (q or "").lower().strip()
    if looks_like_chart_format_correction(q_clean):
        return True
    presentation_patterns = (
        r"(日期|时间).{0,10}(格式|显示)",
        r"(yyyy|mm|dd|hh)[-/:年月日时分秒]",
        r"(保留|显示).{0,6}\d+.{0,4}(位|小数)",
        r"(百分比|千分位|万元|亿元).{0,8}(格式|显示|展示)",
        r"(列宽|对齐|排序样式|表格样式|图表样式)",
    )
    return any(re.search(pattern, q_clean) for pattern in presentation_patterns)


async def resolve_data_query_turn_classification(
    user_query: str,
    messages: Optional[List[Dict[str, str]]],
    *,
    user_info: Optional[Dict] = None,
    conversation_id: Optional[str] = None,
    has_last_data_result: Optional[bool] = None,
) -> tuple[DataQueryTurnClassification, IntentResponse, float]:
    """Resolve ChatBI-internal data-query request classification.

    Router/dispatcher may provide coarse context, but DataQueryExecutor treats this
    result as the final authority for its own execution path.
    """
    if has_last_data_result is None and conversation_id:
        has_last_data_result = await load_last_data_result(user_info, conversation_id) is not None
    has_last_data_result = bool(has_last_data_result)

    q = (user_query or "").strip()
    if looks_like_metadata_query(q):
        classification = _classification_for_turn_type(
            DataQueryTurnType.METADATA_QUERY,
            "检测到元数据或分析口径探索意图，仅需检索数据集定义后回答，不强制执行 SQL",
            skip_intent_llm=True,
        )
        intent_info = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=1.0,
            reasoning=classification.reasoning,
            entities=[],
        )
        return classification, intent_info, 0.0

    if has_last_data_result and looks_like_result_presentation(q):
        classification = _classification_for_turn_type(
            DataQueryTurnType.RESULT_PRESENTATION,
            "检测到样式微调或图表形式变更请求，且存在可复用结构化查询结果，进行短路渲染",
            skip_intent_llm=True,
        )
        intent_info = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=1.0,
            reasoning=classification.reasoning,
            entities=[],
        )
        return classification, intent_info, 0.0

    if looks_like_result_action(q, has_last_data_result=has_last_data_result):
        classification = _classification_for_turn_type(
            DataQueryTurnType.RESULT_ACTION,
            "检测到针对当前数据结果的导出、交付或监控动作，保留在 ChatBI 结果工具链处理",
            skip_intent_llm=True,
        )
        intent_info = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=1.0,
            reasoning=classification.reasoning,
            entities=[],
        )
        return classification, intent_info, 0.0

    if _looks_like_general_chat_or_unsupported(
        q,
        has_last_data_result=has_last_data_result,
        messages=messages,
    ):
        classification = _classification_for_non_data(
            "当前请求不属于 ChatBI 查数需求，应引导用户切换到自动路由或其他智能体",
            skip_intent_llm=True,
        )
        intent_info = IntentResponse(
            intent=IntentType.GENERAL,
            confidence=1.0,
            reasoning=classification.reasoning,
            entities=[],
        )
        return classification, intent_info, 0.0

    if messages and _can_reuse_previous_result(
        q,
        messages,
        has_last_data_result=has_last_data_result,
    ):
        reasoning = (
            "检测到对上一轮数据结果的追问，且存在可复用结构化查询结果，直接复用"
            if has_last_data_result
            else "检测到对上一轮数据结果的追问，且最近对话中已有查数展示，直接复用"
        )
        classification = _classification_for_turn_type(
            DataQueryTurnType.RESULT_ANALYSIS,
            reasoning,
            skip_intent_llm=True,
        )
        intent_info = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=1.0,
            reasoning=classification.reasoning,
            entities=[],
        )
        return classification, intent_info, 0.0

    if not messages and _looks_like_explicit_new_data_query(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="首轮检测到高置信内部数据查询信号，直接进入新数据查询",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
        intent_info = IntentResponse(
            intent=IntentType.DATA_QUERY,
            confidence=1.0,
            reasoning=classification.reasoning,
            entities=[],
        )
        return classification, intent_info, 0.0

    intent_start = time.time()
    classification = await _classify_with_llm(
        q,
        messages,
        has_last_data_result=has_last_data_result,
    )
    intent_elapsed_ms = (time.time() - intent_start) * 1000

    if classification and classification.turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT:
        if not _can_reuse_previous_result(
            q,
            messages,
            has_last_data_result=has_last_data_result,
            allow_contextual_llm_reuse=True,
        ):
            if _looks_like_explicit_new_data_query(q):
                classification = _classification_for_turn_type(
                    DataQueryTurnType.NEW_DATA_QUERY,
                    "无法复用上一轮结果，但当前问题包含明确新数据查询诉求，修正为新数据查询",
                    skip_intent_llm=False,
                )
            else:
                classification = _classification_for_clarification(
                    "当前会话没有可复用的上一轮结构化查询结果，且最近对话中也未展示可查数结果",
                    skip_intent_llm=False,
                )
        elif not _recent_history_supports_reuse(messages):
            if _looks_like_explicit_new_data_query(q):
                classification = _classification_for_turn_type(
                    DataQueryTurnType.NEW_DATA_QUERY,
                    "复用上一轮结果上下文不可信，但当前问题包含明确新数据查询诉求，改按新数据查询处理",
                    skip_intent_llm=False,
                )
            else:
                classification = _classification_for_clarification(
                    "最近对话没有明确的上一轮数据结果上下文，需要先确认是否基于之前的查询结果继续分析",
                    skip_intent_llm=False,
                )

    # 澄清缺口自检：剔除原问题已覆盖的字段；剔空或仅剩可执行软缺口则继续查数。
    if classification and classification.turn_type == DataQueryTurnType.CLARIFICATION_REQUIRED:
        original_fields = tuple(classification.missing_fields or ())
        filtered_fields = filter_satisfied_missing_fields(q, original_fields)
        if filtered_fields != original_fields:
            logger.info(
                "[DataQueryTurnClassifier] 澄清缺口自检：%s → %s",
                original_fields,
                filtered_fields,
            )
            classification.missing_fields = filtered_fields

        if _should_proceed_despite_clarification(q, filtered_fields):
            reason = (
                "无人值守自动化任务禁止澄清拦截，直接继续查数"
                if _is_unattended_automation_query(q)
                else (
                    "原问题已覆盖分类缺口，继续查数"
                    if not filtered_fields and original_fields
                    else "查数对象已足够明确，软性缺口或可纠正的对象缺口交由执行阶段补齐，不拦截澄清"
                )
            )
            logger.info(
                "[DataQueryTurnClassifier] 澄清降级为 new_data_query（missing_fields=%s→%s, reasoning=%s）",
                original_fields,
                filtered_fields,
                classification.reasoning,
            )
            classification = _classification_for_turn_type(
                DataQueryTurnType.NEW_DATA_QUERY,
                reason,
                skip_intent_llm=False,
            )

    # LLM 分类为联邦查询时做规则兜底校验：
    # 联邦升级成本高（额外一次 LLM 计划生成），必须有明确的跨数据集/跨库意图才允许；
    # 若用户问题不包含显式跨源关键词，则降级为普通新数据查询，由后续 schema 预拉取阶段再做精细判断。
    if classification and classification.turn_type == DataQueryTurnType.FEDERATED_DATA_QUERY:
        if not _looks_like_explicit_federated_query(q):
            logger.info(
                "[DataQueryTurnClassifier] LLM 分类为 federated_data_query，但问题中无显式跨源意图关键词，"
                "降级为 new_data_query（原始 reasoning: %s）",
                classification.reasoning,
            )
            classification = _classification_for_turn_type(
                DataQueryTurnType.NEW_DATA_QUERY,
                "LLM 判断为联邦查询，但未检测到明确跨数据集关键词，降级为新数据查询（后续 schema 预拉取阶段可再次升级）",
                skip_intent_llm=False,
            )

    if classification is None and looks_like_context_action(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.CONTEXT_ACTION,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到对已有上下文/结果的动作（保存/导出/记住等）",
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=True,
            intent=IntentType.GENERAL,
        )
    elif classification is None and looks_like_skill_execution(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.SKILL_EXECUTION,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到显式技能执行请求",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
    elif classification is None and _can_reuse_previous_result(
        q,
        messages,
        has_last_data_result=has_last_data_result,
    ):
        reasoning = (
            "请求类别 LLM 未返回有效结果；规则兜底检测到对上一轮数据结果的追问，复用结构化查询结果"
            if has_last_data_result
            else "请求类别 LLM 未返回有效结果；规则兜底检测到对上一轮数据结果的追问，复用最近对话中的查数展示"
        )
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.REUSE_PREVIOUS_RESULT,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
    elif (
        classification is None
        and not _looks_like_explicit_new_data_query(q)
        and looks_like_pure_result_followup(q)
    ):
        classification = _classification_for_clarification(
            "请求类别 LLM 未返回有效结果；检测到结果追问但最近对话中没有可信的可复用数据上下文",
            skip_intent_llm=True,
        )
    elif classification is None and looks_like_compound_query_with_viz(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到查数+可视化复合请求",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )
    elif classification is None and _looks_like_explicit_new_data_query(q):
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="请求类别 LLM 未返回有效结果；规则兜底检测到明确查数关键词，按新数据查询处理",
            requires_fresh_data=True,
            requires_few_shot=True,
            skip_intent_llm=True,
            intent=IntentType.DATA_QUERY,
        )

    if classification is None:
        if not messages:
            classification = _classification_for_non_data(
                "首轮请求缺少高置信内部数据查询证据，且语义分类不可用；按能力帮助安全承接",
                skip_intent_llm=True,
            )
        else:
            classification = DataQueryTurnClassification(
                turn_type=DataQueryTurnType.NEW_DATA_QUERY,
                reasoning="请求类别 LLM 与规则兜底均未返回特殊类别，按新数据查询处理",
                requires_fresh_data=True,
                requires_few_shot=True,
                skip_intent_llm=True,
                intent=IntentType.DATA_QUERY,
            )

    intent_info = IntentResponse(
        intent=classification.intent or IntentType.DATA_QUERY,
        confidence=1.0 if classification.skip_intent_llm else 0.85,
        reasoning=classification.reasoning,
        entities=[],
    )
    return classification, intent_info, intent_elapsed_ms
