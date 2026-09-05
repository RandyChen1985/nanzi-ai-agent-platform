"""Canonical per-turn evidence shared by routing and execution layers."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field


TURN_KIND_LABELS = {
    "general": "通用助手",
    "knowledge": "知识库问答",
    "data_query": "数据查询请求",
    "context_action": "上下文动作",
}


def turn_kind_label(turn_kind: Optional[str]) -> str:
    """Return the UI label for the outer executor kind."""
    normalized = str(turn_kind or "general").strip().lower()
    return TURN_KIND_LABELS.get(normalized, normalized)


def should_inject_ltm(turn_kind: Optional[str]) -> bool:
    """Long-term user context is available for every outer turn kind."""
    return True


def should_inject_memory_recall_hint(turn_kind: Optional[str]) -> bool:
    """Keep the generic cross-session hint out of data and knowledge prompts."""
    return str(turn_kind or "").strip().lower() not in {"data_query", "knowledge"}


def should_run_active_memory_preload(turn_kind: Optional[str]) -> bool:
    """Only general/context turns use active memory preload before execution."""
    return str(turn_kind or "").strip().lower() not in {"data_query", "knowledge"}


def should_inject_user_context(turn_kind: Optional[str]) -> bool:
    """Server-authenticated user context is allowed for every outer turn kind."""
    return True


def default_thought_expanded(turn_kind: Optional[str]) -> bool:
    """Data-query turns open the frontend deep-thinking panel by default."""
    return str(turn_kind or "").strip().lower() == "data_query"


def _value(value: Any, default: Optional[str] = None) -> Optional[str]:
    """Return an enum-like value as normalized text without rejecting legacy values."""
    raw = getattr(value, "value", value)
    if raw is None:
        return default
    text = str(raw).strip()
    return text or default


def _float(value: Any, default: float = 0.0) -> float:
    """Coerce legacy confidence values while keeping malformed evidence fail-safe."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _turn_kind(*, capability: Any, action: Any = None, source: Any = None) -> str:
    """Map route capability evidence to the outer executor kind."""
    capability_text = (_value(capability, "answer") or "answer").strip().lower()
    action_text = (_value(action, "unknown") or "unknown").strip().lower()
    source_text = (_value(source, "unknown") or "unknown").strip().lower()
    if action_text == "save_or_export_context":
        return "context_action"
    if (
        capability_text in {"knowledge_search", "knowledge_base"}
        or source_text == "internal_docs"
        or action_text == "ask_knowledge"
    ):
        return "knowledge"
    if capability_text == "data_query" or action_text == "ask_business_data_or_task":
        return "data_query"
    return "general"


class TurnDecision(BaseModel):
    """One JSON-safe decision snapshot for a user turn.

    RouterService or explicit agent selection creates this object once. The
    dispatcher, prompt assembly, tools, runners, and trace all consume the same
    snapshot. Unknown semantic values are retained as text so a malformed
    decision can be rejected by the route-status and capability gates without
    losing diagnostic evidence.
    """

    model_config = ConfigDict(extra="ignore")

    decision_version: str = "v1"
    turn_kind: str = "general"
    route_status: str = "unknown"
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    secondary_agents: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning: Optional[str] = None
    semantic_intent: Optional[str] = None
    semantic_confidence: float = 0.0
    semantic_reasoning: Optional[str] = None
    source: str = "unknown"
    capability: str = "answer"
    request_reasoning: Optional[str] = None
    delegate_capability: Optional[str] = None
    should_delegate: bool = False
    requires_knowledge_search: bool = False
    allows_data_route: bool = False
    semantic_domain: str = "unknown"
    semantic_operation: str = "unknown"
    fact_kind: str = "unknown"
    freshness_requirement: str = "unknown"
    time_scope: Optional[str] = None
    reference_mode: str = "unknown"
    needs_fresh_data: bool = False
    quick_result_followup: bool = False
    max_age_seconds: Optional[int] = None
    requires_source_timestamp: bool = False
    chatbi_mode: Optional[str] = None
    chatbi_evidence_level: str = "none"
    chatbi_reason: Optional[str] = None
    matched_dataset_ids: list[int] = Field(default_factory=list)
    knowledge_catalog_status: Optional[str] = None
    knowledge_catalog_match_ids: list[str] = Field(default_factory=list)
    knowledge_catalog_match_confidence: str = "none"
    knowledge_fallback_allowed: bool = False
    reusable_result_mode: str = "none"
    reusable_result_id: Optional[str] = None
    reusable_result_reason: Optional[str] = None
    accessible_resources: Optional[str] = None
    turn_labels: list[str] = Field(default_factory=list)
    relation_to_previous: str = "unknown"
    user_action_type: str = "unknown"
    provenance: str = "unknown"
    fast_path: Optional[str] = None
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)

    @classmethod
    def for_direct_agent_selection(
        cls,
        agent_config: Any,
        *,
        stage_timings_ms: Optional[Mapping[str, Any]] = None,
    ) -> "TurnDecision":
        """Create the canonical decision when the caller explicitly selected an Agent."""
        capabilities = {
            str(value).strip().lower()
            for value in (getattr(agent_config, "capabilities", None) or [])
        }
        capability = (
            "data_query"
            if "data_query" in capabilities
            else "knowledge_search"
            if "knowledge_base" in capabilities
            else "answer"
        )
        source = (
            "internal_structured_data"
            if capability == "data_query"
            else "internal_docs"
            if capability == "knowledge_search"
            else "general"
        )
        return cls(
            agent_id=_value(getattr(agent_config, "agent_id", None)),
            agent_name=_value(
                getattr(agent_config, "agent_name", None)
                or getattr(agent_config, "agent_display_name", None)
            ),
            turn_kind=_turn_kind(capability=capability, source=source),
            route_status="resolved",
            source=source,
            capability=capability,
            provenance="direct_agent_selection",
            fast_path="direct_agent_selection",
            stage_timings_ms={
                str(key): _float(value)
                for key, value in (stage_timings_ms or {}).items()
            },
            requires_knowledge_search=capability == "knowledge_search",
            allows_data_route=capability == "data_query",
            evidence=["explicit_agent_selection"],
        )

    @classmethod
    def for_default_main_delegation(
        cls,
        agent_config: Any,
        *,
        stage_timings_ms: Optional[Mapping[str, Any]] = None,
    ) -> "TurnDecision":
        """Create the canonical decision for an unselected request entering Main."""
        return cls(
            agent_id=_value(getattr(agent_config, "agent_id", None)),
            agent_name=_value(
                getattr(agent_config, "agent_name", None)
                or getattr(agent_config, "agent_display_name", None)
            ),
            turn_kind="general",
            route_status="resolved",
            source="general",
            capability="answer",
            reasoning="未指定专家，交由主助手直接回答或自动委派",
            request_reasoning="默认进入主助手自动委派链路",
            provenance="automatic_delegation",
            fast_path="default_main",
            stage_timings_ms={
                str(key): _float(value)
                for key, value in (stage_timings_ms or {}).items()
            },
            evidence=["default_main_agent"],
        )

    @classmethod
    def from_router_components(
        cls,
        *,
        agent_id: str,
        agent_name: Optional[str] = None,
        secondary_agents: Optional[list[str]] = None,
        confidence: float = 0.0,
        reasoning: Optional[str] = None,
        intent_info: Any = None,
        request_decision: Any = None,
        turn_labels: Optional[list[str]] = None,
        relation_to_previous: str = "unknown",
        user_action_type: str = "unknown",
        turn_kind: Optional[str] = None,
        route_status: str = "resolved",
        provenance: str = "router",
        fast_path: Optional[str] = None,
        stage_timings_ms: Optional[Mapping[str, Any]] = None,
    ) -> "TurnDecision":
        """Create the canonical decision from RouterService's evaluated components."""
        source = _value(getattr(request_decision, "source", None), "unknown") or "unknown"
        capability = _value(getattr(request_decision, "capability", None), "answer") or "answer"
        action = _value(user_action_type, "unknown") or "unknown"
        semantic_intent = _value(getattr(intent_info, "intent", None))
        matched_dataset_ids = getattr(request_decision, "matched_dataset_ids", ()) or ()
        evidence = ["router"]
        if intent_info is not None:
            evidence.append("semantic_intent")
        if request_decision is not None:
            evidence.append("request_decision")
        if matched_dataset_ids:
            evidence.append("dataset_match")

        return cls(
            turn_kind=turn_kind or _turn_kind(
                capability=capability,
                action=action,
                source=source,
            ),
            route_status=route_status,
            agent_id=str(agent_id),
            agent_name=_value(agent_name),
            secondary_agents=[str(value) for value in (secondary_agents or [])],
            confidence=_float(confidence),
            reasoning=_value(reasoning),
            semantic_intent=semantic_intent,
            semantic_confidence=_float(getattr(intent_info, "confidence", 0.0)),
            semantic_reasoning=_value(getattr(intent_info, "reasoning", None)),
            source=source,
            capability=capability,
            request_reasoning=_value(getattr(request_decision, "reasoning", None)),
            delegate_capability=_value(
                getattr(request_decision, "delegate_capability", None)
            ),
            should_delegate=bool(getattr(request_decision, "should_delegate", False)),
            requires_knowledge_search=bool(
                getattr(request_decision, "requires_knowledge_search", False)
            ),
            allows_data_route=bool(getattr(request_decision, "allows_data_route", False)),
            semantic_domain=_value(
                getattr(request_decision, "semantic_domain", None)
                or getattr(intent_info, "domain", None),
                "unknown",
            ) or "unknown",
            semantic_operation=_value(
                getattr(request_decision, "semantic_operation", None)
                or getattr(intent_info, "operation", None),
                "unknown",
            ) or "unknown",
            fact_kind=_value(
                getattr(request_decision, "fact_kind", None)
                or getattr(intent_info, "fact_kind", None),
                "unknown",
            ) or "unknown",
            freshness_requirement=_value(
                getattr(request_decision, "freshness_requirement", None)
                or getattr(intent_info, "freshness_requirement", None),
                "unknown",
            ) or "unknown",
            time_scope=_value(
                getattr(request_decision, "time_scope", None)
                or getattr(intent_info, "time_scope", None)
            ),
            reference_mode=_value(
                getattr(request_decision, "reference_mode", None)
                or getattr(intent_info, "reference_mode", None),
                "unknown",
            ) or "unknown",
            needs_fresh_data=bool(
                getattr(request_decision, "needs_fresh_data", None)
                if getattr(request_decision, "needs_fresh_data", None) is not None
                else getattr(intent_info, "needs_fresh_data", False)
            ),
            max_age_seconds=getattr(request_decision, "max_age_seconds", None),
            requires_source_timestamp=bool(
                getattr(request_decision, "requires_source_timestamp", False)
            ),
            chatbi_mode=_value(getattr(request_decision, "chatbi_mode", None)),
            chatbi_evidence_level=_value(
                getattr(request_decision, "chatbi_evidence_level", None), "none"
            ) or "none",
            chatbi_reason=_value(getattr(request_decision, "chatbi_reason", None)),
            matched_dataset_ids=[
                int(value)
                for value in matched_dataset_ids
                if str(value).strip().lstrip("-").isdigit()
            ],
            knowledge_catalog_status=_value(
                getattr(request_decision, "knowledge_catalog_status", None)
            ),
            knowledge_catalog_match_ids=[
                str(value)
                for value in (
                    getattr(request_decision, "knowledge_catalog_match_ids", ()) or ()
                )
                if str(value).strip()
            ],
            knowledge_catalog_match_confidence=_value(
                getattr(request_decision, "knowledge_catalog_match_confidence", None),
                "none",
            ) or "none",
            knowledge_fallback_allowed=bool(
                getattr(request_decision, "knowledge_fallback_allowed", False)
            ),
            turn_labels=[str(value) for value in (turn_labels or [])],
            relation_to_previous=_value(relation_to_previous, "unknown") or "unknown",
            user_action_type=action,
            provenance=provenance,
            fast_path=fast_path,
            stage_timings_ms={
                str(key): _float(value)
                for key, value in (stage_timings_ms or {}).items()
            },
            evidence=evidence,
        )

    def to_request_decision(self) -> Any:
        """Project the snapshot back to the existing fail-closed request contract."""
        from app.services.ai.request_decision import (
            RequestCapability,
            RequestDecision,
            RequestSource,
        )

        try:
            source = RequestSource(self.source)
        except ValueError:
            source = RequestSource.UNKNOWN
        try:
            capability = RequestCapability(self.capability)
        except ValueError:
            capability = RequestCapability.ANSWER

        allows_data_route = bool(self.allows_data_route)
        if capability != RequestCapability.DATA_QUERY:
            allows_data_route = False

        return RequestDecision(
            source=source,
            capability=capability,
            confidence=self.confidence,
            reasoning=self.request_reasoning or self.reasoning or "统一轮次决策",
            should_delegate=bool(self.should_delegate),
            delegate_capability=self.delegate_capability,
            requires_knowledge_search=bool(self.requires_knowledge_search),
            allows_data_route=allows_data_route,
            semantic_intent=self.semantic_intent,
            semantic_confidence=self.semantic_confidence,
            semantic_domain=self.semantic_domain,
            semantic_operation=self.semantic_operation,
            fact_kind=self.fact_kind,
            freshness_requirement=self.freshness_requirement,
            time_scope=self.time_scope,
            reference_mode=self.reference_mode,
            needs_fresh_data=bool(self.needs_fresh_data),
            max_age_seconds=self.max_age_seconds,
            requires_source_timestamp=bool(self.requires_source_timestamp),
            chatbi_mode=self.chatbi_mode,
            chatbi_evidence_level=self.chatbi_evidence_level,
            chatbi_reason=self.chatbi_reason,
            matched_dataset_ids=tuple(self.matched_dataset_ids),
            knowledge_catalog_status=self.knowledge_catalog_status,
            knowledge_catalog_match_ids=tuple(self.knowledge_catalog_match_ids),
            knowledge_catalog_match_confidence=self.knowledge_catalog_match_confidence,
            knowledge_fallback_allowed=bool(self.knowledge_fallback_allowed),
        )

    def trace_payload(
        self,
        *,
        stage_timings_ms: Optional[Mapping[str, Any]] = None,
        executor: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return safe decision telemetry without raw model reasoning or credentials."""
        timings = dict(self.stage_timings_ms)
        timings.update(
            {
                str(key): _float(value)
                for key, value in (stage_timings_ms or {}).items()
            }
        )
        payload: dict[str, Any] = {
            "decision_version": self.decision_version,
            "provenance": self.provenance,
            "fast_path": self.fast_path,
            "route_source": self.source,
            "capability": self.capability,
            "semantic_intent": self.semantic_intent,
            "knowledge_catalog_status": self.knowledge_catalog_status,
            "knowledge_catalog_match_confidence": self.knowledge_catalog_match_confidence,
            "knowledge_catalog_match_ids": list(self.knowledge_catalog_match_ids),
            "knowledge_fallback_allowed": bool(self.knowledge_fallback_allowed),
            "reusable_result_mode": self.reusable_result_mode,
            "reusable_result_id": self.reusable_result_id,
            "reusable_result_reason": self.reusable_result_reason,
            "evidence": list(self.evidence),
            "stage_timings_ms": timings,
        }
        if executor:
            payload["executor"] = executor
        return payload

__all__ = ["TurnDecision"]
