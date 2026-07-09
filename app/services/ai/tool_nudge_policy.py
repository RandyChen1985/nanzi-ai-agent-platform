"""工具促发（Tool Nudge）：由「本轮实际绑定的工具 + 其描述」驱动。

当用户问题与某个已绑定工具的能力（name + description）明显相关、且本轮属于
「应当用工具拿真实结果」的意图时，在 System Prompt 顶部注入一条强约束便签，
提高模型主动调用工具的概率。

设计要点：
- **不写死类别/工具名**：候选完全来自运行时 `tools` 的 name + description，
  因此平台内置工具、通用 API 工具、MCP 工具都能被自动促发。
- **不新增 LLM 调用**：相关度用字符级 bigram 重叠（无第三方分词依赖，中英通用）。
- **门禁过滤**：问候 / 元操作 / 过短问题不促发；记忆类有专门的 memory_search 便签。
- **命中至多一条**：取相关度最高的工具，避免 prompt 噪声与误触发。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Set

from app.services.ai.intent_service import (
    IntentSource as ToolIntentSource,
    IntentSourceFrame as ToolIntentFrame,
    looks_like_platform_self_service_query,
    resolve_intent_source,
)

# 不主动促发的工具（写入/管理/记忆维护类）：避免推动模型产生副作用或与专门机制重复。
_NUDGE_EXCLUDED_TOOLS = frozenset({
    "update_user_preference",
    "delete_user_preference",
    "fetch_user_long_term_memory",
    "memory_search",
    "create_skills",
    "update_dashboard_context",
})

# 计算相关度时剔除的高频泛化片段（出现在问题里但无区分度）。
_STOP_FRAGMENTS = frozenset({
    "帮我", "帮忙", "一下", "一个", "请问", "可以", "怎么", "如何", "什么", "哪些",
    "有没有", "能否", "现在", "目前", "我想", "我要", "你能", "麻烦", "看下", "看看",
    "the", "and", "for", "with", "this", "that", "what", "how", "please", "help",
})

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ALNUM_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{1,}")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _query_signals(query: str) -> Set[str]:
    """从用户问题提取匹配信号：中文字符 bigram + 英文/数字词（去停用片段）。"""
    raw = (query or "").lower()
    signals: Set[str] = set()

    for run in _CJK_RUN.findall(raw):
        if len(run) == 1:
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram in _STOP_FRAGMENTS:
                continue
            signals.add(bigram)

    for token in _ALNUM_TOKEN.findall(raw):
        if token in _STOP_FRAGMENTS:
            continue
        signals.add(token)

    return signals


def _tool_text(tool: Any) -> str:
    name = str(getattr(tool, "name", "") or "")
    description = str(getattr(tool, "description", "") or "")
    return _normalize(f"{name} {description}")


def relevance_score(query_signals: Set[str], tool_text: str) -> float:
    """问题信号在工具 name+description 文本中的覆盖比例。"""
    if not query_signals or not tool_text:
        return 0.0
    matched = sum(1 for sig in query_signals if sig in tool_text)
    return matched / len(query_signals)


def should_consider_tool_nudge(user_query: str) -> bool:
    """门禁：过滤问候 / 元操作 / 过短问题。"""
    query = (user_query or "").strip()
    if len(query) < 4:
        return False

    from app.services.ai.intent_service import looks_like_greeting, looks_like_meta_action

    if looks_like_greeting(query) or looks_like_meta_action(query):
        return False
    return True


# 相关度 ≥ 该阈值时，hard 模式可直接强制调用「该具体工具」；介于 min 与此之间则强制
# 「必须调某工具（required）」由模型自行在已绑定工具中挑选。
STRONG_FORCE_SCORE = 0.5


@dataclass(frozen=True)
class ToolNudge:
    tool_name: str
    score: float
    message: str
    force_first_call: bool = False

    def recommended_force_mode(self) -> str:
        """hard 模式下推荐 of ToolChoice.mode：高相关度锁定具体工具，否则 required。"""
        if self.score >= STRONG_FORCE_SCORE:
            return self.tool_name
        return "required"

    @property
    def should_force_first_call(self) -> bool:
        return self.force_first_call


def _short_capability(tool: Any) -> str:
    description = str(getattr(tool, "description", "") or "").strip()
    if not description:
        return ""
    # 取首句作为能力摘要，避免把整段 docstring 灌进 prompt。
    first = re.split(r"[。\n]", description, maxsplit=1)[0].strip()
    return first[:80]


def _build_message(tool_name: str, capability: str) -> str:
    cap = f"（{capability}）" if capability else ""
    return (
        f"【本轮工具优先】已绑定工具「{tool_name}」{cap}很可能可直接获取用户需要的真实结果。"
        f"请先调用它（或其他更合适的已绑定工具）拿到结果再回答；"
        f"在获得工具返回之前，不要凭记忆或常识直接给出具体数值、路径、状态或结论；"
        f"若工具返回为空或失败，如实说明，不要编造。"
    )


_NOTIFICATION_ACTION_TERMS = (
    "发送", "推送", "通知", "发到", "发给", "发一下", "send", "push", "notify",
)

_NOTIFICATION_CHANNELS = (
    (
        "send_dingtalk_message",
        ("钉钉", "dingtalk"),
        "钉钉群机器人",
    ),
    (
        "send_wechat_work_message",
        ("企微", "企业微信", "wechat work", "wecom"),
        "企业微信群机器人",
    ),
    (
        "send_email",
        ("邮件", "邮箱", "email", "mail"),
        "邮件",
    ),
)


_EXPLICIT_SUB_AGENT_ACTION_TERMS = (
    "调用", "委派", "委派给", "交给", "让", "使用", "用",
    "call", "delegate", "ask",
)

_SELF_AGENT_NAMES = frozenset({
    "main",
    "assistant",
    "主助手",
    "主智能体",
    "通用助手",
    "通用智能体",
})


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term and term in text for term in terms)


def _sub_agent_aliases(name: str) -> Set[str]:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return set()
    return {
        normalized,
        normalized.replace("_", "-"),
        normalized.replace("-", "_"),
    }


def _contains_sub_agent_alias(query: str, alias: str) -> bool:
    if not alias:
        return False
    if re.search(r"[a-z0-9_-]", alias):
        pattern = rf"(?<![a-z0-9_-]){re.escape(alias)}(?![a-z0-9_-])"
        return re.search(pattern, query) is not None
    return alias in query


def _resolve_explicit_sub_agent_target(
    query: str,
    available_sub_agent_names: Optional[Set[str]],
) -> Optional[str]:
    """用户点名某个可用子代理时，返回其规范名称。"""
    if not available_sub_agent_names:
        return None

    normalized_query = (query or "").strip().lower()
    if not _contains_any(normalized_query, _EXPLICIT_SUB_AGENT_ACTION_TERMS):
        return None

    query_variants = {
        normalized_query,
        normalized_query.replace("_", "-"),
        normalized_query.replace("-", "_"),
    }
    for candidate in sorted(available_sub_agent_names, key=lambda item: len(str(item)), reverse=True):
        canonical = str(candidate or "").strip()
        if not canonical:
            continue
        if canonical.lower() in _SELF_AGENT_NAMES:
            continue
        aliases = _sub_agent_aliases(canonical)
        if any(
            _contains_sub_agent_alias(query_variant, alias)
            for query_variant in query_variants
            for alias in aliases
        ):
            return canonical
    return None


def _build_explicit_sub_agent_message(target_agent_name: str) -> str:
    return (
        f"【本轮工具优先】用户明确要求调用子代理 '{target_agent_name}'。"
        f"你必须优先调用 sub_agent_call(agent_name='{target_agent_name}', query='用户的问题') "
        f"委派给该子代理处理，拿到结果后再回答；"
        f"不要改派给其他子代理，也不要在未调用工具前自行完成该任务；"
        f"若工具返回为空或失败，如实说明。"
    )


def _resolve_notification_nudge(query: str, tools: List[Any]) -> Optional[ToolNudge]:
    normalized_query = query.lower()
    if not _contains_any(normalized_query, _NOTIFICATION_ACTION_TERMS):
        return None

    available_tool_names = {
        str(getattr(tool, "name", "") or "")
        for tool in (tools or [])
    }
    for tool_name, channel_terms, channel_label in _NOTIFICATION_CHANNELS:
        if tool_name not in available_tool_names:
            continue
        if not _contains_any(normalized_query, channel_terms):
            continue
        return ToolNudge(
            tool_name=tool_name,
            score=1.0,
            message=(
                f"【本轮工具优先】用户明确要求发送或推送到{channel_label}。"
                f"请调用已绑定工具「{tool_name}」完成发送；该工具会自动读取当前用户"
                f"在个人中心 -> 消息通知里的配置，无需用户在本轮提供 webhook、token 或服务器配置。"
                f"只有工具返回失败时，才向用户说明配置或发送失败原因；不要在未调用工具前声称未配置。"
            ),
        )
    return None


def resolve_tool_nudge(
    user_query: str,
    tools: List[Any],
    *,
    min_score: float = 0.25,
    exclude_tools: Optional[Set[str]] = None,
    available_sub_agent_names: Optional[Set[str]] = None,
    sub_agent_targets_by_capability: Optional[Mapping[str, str]] = None,
    semantic_intent: Any = None,
    semantic_confidence: Any = None,
    turn_intent: Any = None,
) -> Optional[ToolNudge]:
    """解析本轮是否需要工具促发；返回相关度最高的一条便签或 None。

    相关度完全由 ``tools`` 的 name + description 与问题的字符重叠决定，
    不依赖任何写死的工具名或类别。
    """
    query = (user_query or "").strip()
    if not query or not should_consider_tool_nudge(query):
        return None
    intent_frame = resolve_intent_source(
        query,
        semantic_intent=semantic_intent,
        semantic_confidence=semantic_confidence,
        turn_intent=turn_intent,
    )
    is_platform_self_service = looks_like_platform_self_service_query(query)

    # 特殊规则：对于强查数或强知识库检索意图，若绑定了 sub_agent_call，优先做静默子代理委派
    sub_agent_tool = next((t for t in (tools or []) if getattr(t, "name", "") == "sub_agent_call"), None)
    if sub_agent_tool:
        explicit_sub_agent = _resolve_explicit_sub_agent_target(query, available_sub_agent_names)
        if explicit_sub_agent:
            return ToolNudge(
                tool_name="sub_agent_call",
                score=1.0,
                message=_build_explicit_sub_agent_message(explicit_sub_agent),
                force_first_call=True,
            )

    if sub_agent_tool and not is_platform_self_service:
        def _sub_agent_available(name: str) -> bool:
            if available_sub_agent_names is None:
                return True
            aliases = {name, name.replace("_", "-"), name.replace("-", "_")}
            return bool(aliases & available_sub_agent_names)

        def _target_for_capability(capability: str, fallback_name: str) -> Optional[str]:
            target = ""
            if sub_agent_targets_by_capability:
                target = str(sub_agent_targets_by_capability.get(capability) or "").strip()
            if target:
                return target if _sub_agent_available(target) else None
            return fallback_name if _sub_agent_available(fallback_name) else None

        # 优先判断更具体的知识库检索意图
        if intent_frame.source == ToolIntentSource.INTERNAL_DOCS:
            target_agent_name = _target_for_capability("knowledge_base", "knowledge-base")
            if not target_agent_name:
                return None
            desc = (
                "用户问题涉及内部制度、SOP或操作规程查询。主助手没有直接读取知识库能力，"
                f"你必须优先调用 sub_agent_call(agent_name='{target_agent_name}', query='用户的问题') 委派给知识库助手 '{target_agent_name}' 检索文档，拿到结果再回答；"
                "严禁凭记忆直接编造任何文档或规范；若工具返回为空或失败，如实说明，不要编造。"
            )
            return ToolNudge(
                tool_name="sub_agent_call",
                score=0.95,
                message=f"【本轮工具优先】本轮用户请求涉及制度、SOP或规范文档检索。{desc}",
                force_first_call=True,
            )
        elif intent_frame.source == ToolIntentSource.INTERNAL_STRUCTURED_DATA:
            target_agent_name = _target_for_capability("data_query", "chat-bi")
            if not target_agent_name:
                return None
            desc = (
                "用户问题涉及内部数据、指标或资产查询。主助手没有直接连接数据库能力，"
                f"你必须优先调用 sub_agent_call(agent_name='{target_agent_name}', query='用户的问题') 委派给数据智能助手 '{target_agent_name}' 获取真实数据，拿到结果再回答；"
                "严禁凭记忆直接编造任何表格、数据或字段；若工具返回为空或失败，如实说明，不要编造。"
            )
            return ToolNudge(
                tool_name="sub_agent_call",
                score=0.95,
                message=f"【本轮工具优先】本轮用户请求涉及内部数据、指标或资产查询。{desc}",
                force_first_call=True,
            )

    notification_nudge = _resolve_notification_nudge(query, tools)
    if notification_nudge is not None:
        return notification_nudge

    signals = _query_signals(query)
    if len(signals) < 2:
        return None

    excluded = set(_NUDGE_EXCLUDED_TOOLS)
    if exclude_tools:
        excluded |= {str(name) for name in exclude_tools}
    if is_platform_self_service:
        excluded.add("sub_agent_call")

    best_tool: Any = None
    best_score = 0.0
    for tool in tools or []:
        name = str(getattr(tool, "name", "") or "")
        if not name or name in excluded:
            continue
        score = relevance_score(signals, _tool_text(tool))
        if score > best_score:
            best_score = score
            best_tool = tool

    effective_min_score = 0.2 if intent_frame.source == ToolIntentSource.PUBLIC_WEB else min_score
    if best_tool is None or best_score < effective_min_score:
        return None

    tool_name = str(getattr(best_tool, "name", "") or "")
    return ToolNudge(
        tool_name=tool_name,
        score=round(best_score, 3),
        message=_build_message(tool_name, _short_capability(best_tool)),
    )
