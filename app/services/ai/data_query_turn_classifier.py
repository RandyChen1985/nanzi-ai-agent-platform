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
    METADATA_QUERY = "metadata_query"
    REUSE_PREVIOUS_RESULT = "reuse_previous_result"
    CONTEXT_ACTION = "context_action"
    SKILL_EXECUTION = "skill_execution"
    CLARIFICATION_OR_NON_DATA = "clarification_or_non_data"
    NON_DATA_REQUEST = "non_data_request"
    CLARIFICATION_REQUIRED = "clarification_required"
    FORMAT_CORRECTION = "format_correction"
    FEDERATED_DATA_QUERY = "federated_data_query"


DATA_QUERY_TURN_TYPE_LABELS: dict[DataQueryTurnType, str] = {
    DataQueryTurnType.NEW_DATA_QUERY: "新数据查询",
    DataQueryTurnType.METADATA_QUERY: "元数据探索",
    DataQueryTurnType.REUSE_PREVIOUS_RESULT: "复用上一轮结果",
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
    if turn_type == DataQueryTurnType.REUSE_PREVIOUS_RESULT:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.DATA_QUERY,
        )
    if turn_type == DataQueryTurnType.FORMAT_CORRECTION:
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
    if turn_type == DataQueryTurnType.CONTEXT_ACTION:
        return DataQueryTurnClassification(
            turn_type=turn_type,
            reasoning=reasoning,
            requires_fresh_data=False,
            requires_few_shot=False,
            requires_sql_query=False,
            skip_intent_llm=skip_intent_llm,
            intent=IntentType.GENERAL,
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
        turn_type=DataQueryTurnType.NEW_DATA_QUERY,
        reasoning=reasoning,
        requires_fresh_data=True,
        requires_few_shot=True,
        skip_intent_llm=skip_intent_llm,
        intent=IntentType.DATA_QUERY,
    )


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
    ]
    return any(keyword in q for keyword in keywords) or _looks_like_business_status_data_query(q)


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

1. new_data_query：需要重新查询业务数据。
2. metadata_query：对当前有权访问的数据集结构、表结构、可查询字段、分析口径、元数据信息的探索提问（例如“说明智能体有哪些字段”、“支持查询哪些指标”、“列名有啥”）。
3. reuse_previous_result：不需要重新查库，只是基于上一轮结构化查询结果做展示、格式化、分析、总结、可视化、改列格式、排序说明等。
4. context_action：对已有上下文或上一轮结果执行保存、导出、发送、记住、沉淀为技能等动作。
5. skill_execution：显式要求使用/执行某个技能。
6. non_data_request：身份、模型、能力、闲聊、写作、翻译、通用知识等非查数请求。
7. clarification_required：已确认用户想查业务数据，但缺少安全执行所需的数据对象、时间范围、指标、维度或结果上下文。
8. format_correction：不需要重新查库，只是对图表的展现形式或样式进行微调（如将折线图改为柱状图、给特定数据标红、添加图表参考线等样式调整）。
9. federated_data_query：用户显式要求跨数据集/跨库/跨源/联邦/联合查询，或明确要求关联多个数据集、数据源、库或表。

约束：
- 如果选择 reuse_previous_result 或 format_correction，必须确认“存在上一轮结构化查询结果”为 true。
- 如果用户提出新的查询对象、时间范围或筛选条件，选择 new_data_query。
- 如果用户明确要求跨数据集、跨库、联邦查询，或要求关联多个数据集/数据源/库/表，选择 federated_data_query。
- 如果用户只是提问元数据字段/分析口径，选择 metadata_query。
- clarification_required 必须返回至少一个 missing_fields，可选值：data_object、metric、time_range、dimension、result_context、dataset_or_schema。
- 只返回 JSON，不要解释，不要 Markdown。

JSON 格式：
{{"turn_type":"new_data_query|metadata_query|reuse_previous_result|context_action|skill_execution|non_data_request|clarification_required|format_correction|federated_data_query","reasoning":"一句中文原因","missing_fields":[]}}

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
        "字段定义", "分析口径", "字段列表", "口径说明"
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

    if has_last_data_result and looks_like_chart_format_correction(q):
        classification = _classification_for_turn_type(
            DataQueryTurnType.FORMAT_CORRECTION,
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
            DataQueryTurnType.REUSE_PREVIOUS_RESULT,
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

    if not messages:
        classification = DataQueryTurnClassification(
            turn_type=DataQueryTurnType.NEW_DATA_QUERY,
            reasoning="首轮会话无历史，自动归类为新数据查询以降低首包延迟",
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
    elif classification is None and looks_like_pure_result_followup(q):
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
