"""统一请求来源与能力判定。

这个模块只回答一件事：用户本轮到底需要哪类信息来源/能力。
它不选择具体工具名或具体智能体，避免分类层和运行时工具绑定耦合。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Optional

from app.services.ai.chatbi_qualification import ChatBIQualification, ChatBIMode

from app.services.ai.intent_service import (
    IntentType,
    looks_like_accessible_resource_catalog_query,
    looks_like_context_action,
    looks_like_data_followup,
    looks_like_dynamic_public_fact_query,
    looks_like_general_query,
    looks_like_greeting,
    looks_like_knowledge_query,
    looks_like_meta_action,
    looks_like_platform_self_service_query,
    looks_like_public_profile_lookup,
    looks_like_pure_result_followup,
    looks_like_runtime_diagnostic_query,
    looks_like_short_field_or_continuation_followup,
    looks_like_strong_business_data_request,
    looks_like_web_search_query,
)


class RequestSource(str, Enum):
    INTERNAL_STRUCTURED_DATA = "internal_structured_data"
    INTERNAL_DOCS = "internal_docs"
    PUBLIC_WEB = "public_web"
    PLATFORM_SELF_HELP = "platform_self_help"
    CONVERSATION_CONTEXT = "conversation_context"
    RUNTIME_DIAGNOSTIC = "runtime_diagnostic"
    GENERAL = "general"
    UNKNOWN = "unknown"


class RequestCapability(str, Enum):
    ANSWER = "answer"
    # Backward-compatible value: data_query is the platform's ChatBI query
    # capability, not a generic capability for every structured fact lookup.
    DATA_QUERY = "data_query"
    KNOWLEDGE_SEARCH = "knowledge_search"
    WEB_SEARCH = "web_search"
    CONTEXT_TRANSFORM = "context_transform"
    RUNTIME_TOOL = "runtime_tool"


@dataclass(frozen=True)
class RequestDecision:
    source: RequestSource
    capability: RequestCapability
    confidence: float
    reasoning: str
    should_delegate: bool = False
    delegate_capability: Optional[str] = None
    requires_knowledge_search: bool = False
    allows_data_route: bool = False
    semantic_intent: Optional[str] = None
    semantic_confidence: float = 0.0
    chatbi_mode: Optional[str] = None
    chatbi_evidence_level: str = "none"
    chatbi_reason: Optional[str] = None
    matched_dataset_ids: tuple[int, ...] = ()


def _intent_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().upper()


def _coerce_confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _decision(
    source: RequestSource,
    capability: RequestCapability,
    confidence: float,
    reasoning: str,
    *,
    should_delegate: bool = False,
    delegate_capability: Optional[str] = None,
    requires_knowledge_search: bool = False,
    allows_data_route: bool = False,
    semantic_name: str = "",
    semantic_confidence: float = 0.0,
) -> RequestDecision:
    return RequestDecision(
        source=source,
        capability=capability,
        confidence=confidence,
        reasoning=reasoning,
        should_delegate=should_delegate,
        delegate_capability=delegate_capability,
        requires_knowledge_search=requires_knowledge_search,
        allows_data_route=allows_data_route,
        semantic_intent=semantic_name or None,
        semantic_confidence=semantic_confidence,
    )


def apply_chatbi_qualification(
    decision: RequestDecision,
    qualification: ChatBIQualification,
) -> RequestDecision:
    """Apply the ChatBI evidence gate without changing other capabilities."""
    is_chatbi_decision = (
        decision.capability == RequestCapability.DATA_QUERY
        or decision.delegate_capability == RequestCapability.DATA_QUERY.value
    )
    if qualification.mode == ChatBIMode.DIRECT and is_chatbi_decision:
        allows_data_route = True
        should_delegate = decision.should_delegate
    elif qualification.mode == ChatBIMode.CLARIFY and is_chatbi_decision:
        # A direct ChatBI route may ask the user to choose a dataset, but a
        # General agent must not silently spend a sub-agent call on it.
        allows_data_route = True
        should_delegate = False
    elif is_chatbi_decision:
        allows_data_route = False
        should_delegate = False
    else:
        # Carry the qualification metadata even when another, stronger
        # capability (runtime tool, web, knowledge, etc.) already won.
        allows_data_route = decision.allows_data_route
        should_delegate = decision.should_delegate

    return replace(
        decision,
        allows_data_route=allows_data_route,
        should_delegate=should_delegate,
        delegate_capability=(
            decision.delegate_capability if should_delegate else None
        ),
        chatbi_mode=qualification.mode.value,
        chatbi_evidence_level=qualification.evidence_level,
        chatbi_reason=qualification.reason,
        matched_dataset_ids=qualification.matched_dataset_ids,
    )


def resolve_request_decision(
    query: str,
    *,
    semantic_intent: Any = None,
    semantic_confidence: Any = None,
    turn_intent: Any = None,
    has_last_data_result: bool = False,
    has_knowledge_binding: bool = False,
    semantic_intent_blocks_followup: bool = False,
) -> RequestDecision:
    """统一判断请求来源、所需能力和是否允许委派。

    判定顺序按“强边界优先”排列：平台自服务、公网、运行环境、通用问答等先拦截，
    内部知识库/ChatBI 业务数据只在有明确来源证据时才打开。
    """
    q = (query or "").strip()
    semantic_name = _intent_name(semantic_intent)
    semantic_score = _coerce_confidence(semantic_confidence)
    turn_name = _intent_name(turn_intent)
    effective_intent = semantic_name or turn_name

    if not q:
        return _decision(
            RequestSource.UNKNOWN,
            RequestCapability.ANSWER,
            0.0,
            "empty query",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_meta_action(q) or looks_like_context_action(q) or looks_like_greeting(q):
        return _decision(
            RequestSource.GENERAL,
            RequestCapability.ANSWER,
            0.95,
            "meta/context/greeting request handled by main assistant",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_platform_self_service_query(q):
        return _decision(
            RequestSource.PLATFORM_SELF_HELP,
            RequestCapability.ANSWER,
            0.95,
            "platform self-service or skills/tools configuration query",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_accessible_resource_catalog_query(q):
        return _decision(
            RequestSource.PLATFORM_SELF_HELP,
            RequestCapability.ANSWER,
            0.94,
            "accessible dataset/knowledge-base catalog listing",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_web_search_query(q):
        return _decision(
            RequestSource.PUBLIC_WEB,
            RequestCapability.WEB_SEARCH,
            0.95,
            "explicit public web/search signal",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_runtime_diagnostic_query(q):
        return _decision(
            RequestSource.RUNTIME_DIAGNOSTIC,
            RequestCapability.RUNTIME_TOOL,
            0.9,
            "current runtime/system diagnostic signal",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_dynamic_public_fact_query(q):
        return _decision(
            RequestSource.PUBLIC_WEB,
            RequestCapability.WEB_SEARCH,
            0.9,
            "dynamic public fact requires refreshed external evidence",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_public_profile_lookup(q):
        return _decision(
            RequestSource.PUBLIC_WEB,
            RequestCapability.WEB_SEARCH,
            0.85,
            "public profile/company lookup signal",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    has_data_semantics = (
        semantic_name == IntentType.DATA_QUERY.value
        or turn_name == IntentType.DATA_QUERY.value
    )
    has_explicit_context_transform_signal = (
        looks_like_data_followup(q)
        or looks_like_pure_result_followup(q)
    )
    has_short_context_signal = looks_like_short_field_or_continuation_followup(q)
    has_context_transform_signal = has_explicit_context_transform_signal or (
        has_short_context_signal and (has_last_data_result or has_data_semantics)
    )
    if has_context_transform_signal:
        context_is_data = (
            has_last_data_result
            or has_data_semantics
        )
        if semantic_intent_blocks_followup and semantic_name in {
            IntentType.GENERAL.value,
            IntentType.KNOWLEDGE_BASE.value,
            IntentType.UNKNOWN.value,
        }:
            context_is_data = False
        return _decision(
            RequestSource.CONVERSATION_CONTEXT,
            RequestCapability.CONTEXT_TRANSFORM,
            0.9 if context_is_data else 0.82,
            (
                "context transform over previous structured-data result"
                if context_is_data
                else "context transform over previous non-data conversation"
            ),
            should_delegate=context_is_data,
            delegate_capability="data_query" if context_is_data else None,
            allows_data_route=context_is_data,
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    # 意图已确认为 DATA_QUERY 时，不再依赖强业务关键词；统一打开路由/委派闸门，
    # 由主助手强制 sub_agent_call 或直接路由到 data_query 智能体。
    # 平台自助/公网/运行诊断等更强边界已在上方优先拦截。
    if has_data_semantics:
        return _decision(
            RequestSource.INTERNAL_STRUCTURED_DATA,
            RequestCapability.DATA_QUERY,
            max(semantic_score, 0.86),
            (
                "ChatBI internal structured data signal"
                if looks_like_strong_business_data_request(q)
                else "confirmed data_query intent requires data agent"
            ),
            should_delegate=True,
            delegate_capability="data_query",
            allows_data_route=True,
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if looks_like_general_query(q) or semantic_name == IntentType.GENERAL.value:
        return _decision(
            RequestSource.GENERAL,
            RequestCapability.ANSWER,
            max(semantic_score, 0.8),
            "general public/common/programming/text request",
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if semantic_name == IntentType.KNOWLEDGE_BASE.value or looks_like_knowledge_query(q):
        return _decision(
            RequestSource.INTERNAL_DOCS,
            RequestCapability.KNOWLEDGE_SEARCH,
            max(semantic_score, 0.85),
            "internal documentation/SOP/knowledge-base request",
            should_delegate=True,
            delegate_capability="knowledge_base",
            requires_knowledge_search=True,
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    if has_knowledge_binding and semantic_name not in {
        IntentType.DATA_QUERY.value,
        IntentType.GENERAL.value,
    }:
        return _decision(
            RequestSource.INTERNAL_DOCS,
            RequestCapability.KNOWLEDGE_SEARCH,
            max(semantic_score, 0.72),
            "knowledge binding is explicit and no stronger non-knowledge boundary matched",
            should_delegate=True,
            delegate_capability="knowledge_base",
            requires_knowledge_search=True,
            semantic_name=effective_intent,
            semantic_confidence=semantic_score,
        )

    return _decision(
        RequestSource.UNKNOWN,
        RequestCapability.ANSWER,
        semantic_score,
        (
            f"semantic intent is {effective_intent} without reliable internal source"
            if effective_intent
            else "no reliable source signal"
        ),
        semantic_name=effective_intent,
        semantic_confidence=semantic_score,
    )
