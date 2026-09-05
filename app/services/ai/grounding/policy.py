from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.services.ai.grounding.contract import (
    EvidenceContract,
    EvidenceContractMode,
    EvidenceDecisionOrigin,
)
from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType, FactFreshness
from app.services.ai.request_decision import RequestDecision, RequestSource


class GroundingAction(str, Enum):
    PASS = "pass"
    PASS_WITH_WARNING = "pass_with_warning"
    PASS_WITHOUT_FACTS = "pass_without_facts"
    BLOCK_UNGROUNDED_FACTS = "block_ungrounded_facts"


class GroundingRiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class FactRequirement:
    required: bool
    accepted_types: frozenset[EvidenceType]
    scrutinize_unknown_output: bool = False
    freshness: FactFreshness = FactFreshness.UNKNOWN
    max_age_seconds: int | None = None
    requires_source_timestamp: bool = False
    allow_conversation_reuse: bool = False
    # 截断结果可支持单条事实，但不能支持“全部/总数”等完整性结论。
    allow_truncated: bool = True
    time_scope: str | None = None
    block_unsupported_facts: bool = False
    evidence_mode: str = EvidenceContractMode.NONE.value
    decision_origin: str = EvidenceDecisionOrigin.FALLBACK.value
    decision_confidence: float = 0.0
    decision_conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroundingDecision:
    action: GroundingAction
    reason: str
    required_evidence_types: frozenset[EvidenceType] = frozenset()
    available_evidence_types: frozenset[EvidenceType] = frozenset()
    risk_level: GroundingRiskLevel = GroundingRiskLevel.NONE


_SOURCE_EVIDENCE_TYPES = {
    RequestSource.INTERNAL_STRUCTURED_DATA: frozenset({EvidenceType.INTERNAL_DATA}),
    RequestSource.INTERNAL_DOCS: frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
    RequestSource.PUBLIC_WEB: frozenset({EvidenceType.PUBLIC_WEB}),
    RequestSource.RUNTIME_DIAGNOSTIC: frozenset({EvidenceType.RUNTIME_STATE}),
}

_CAPABILITY_EVIDENCE_TYPES = {
    "data_query": EvidenceType.INTERNAL_DATA,
    "knowledge_base": EvidenceType.INTERNAL_KNOWLEDGE,
    "knowledge_search": EvidenceType.INTERNAL_KNOWLEDGE,
    "web_search": EvidenceType.PUBLIC_WEB,
    "runtime_tool": EvidenceType.RUNTIME_STATE,
    "runtime_diagnostic": EvidenceType.RUNTIME_STATE,
    "file_read": EvidenceType.USER_FILE,
    "user_file": EvidenceType.USER_FILE,
    "memory_search": EvidenceType.CONVERSATION_MEMORY,
}

_HYPOTHETICAL_MARKERS = ("假设", "示例", "虚构", "模拟数据", "仅用于演示")

_MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|){2,}")
_FACT_VALUE_RE = re.compile(
    r"(?:[¥￥$€£]\s*\d|\d[\d,.]*\s*(?:%|％|万|亿|元|美元|条|台|个|人|次))",
    re.IGNORECASE,
)
_EXECUTION_CLAIM_RE = re.compile(
    r"(?:已经|已|刚刚|成功|根据).{0,10}(?:调用|查询|检索|搜索|读取|检查|执行).{0,12}(?:结果|到|完成|成功)?",
    re.IGNORECASE,
)
_EXECUTION_SUMMARY_RE = re.compile(
    r"(?:"
    r"(?:已|已经|刚刚|成功|完成|完成后).{0,12}"
    r"(?:调用|查询|检索|搜索|读取|检查|执行|同步|处理).{0,12}"
    r"(?:完成|成功|结束|同步|处理|结果)?(?:了|啊|呢|呀|啦)?"
    r"|"
    r"(?:查询|检索|搜索|读取|执行|同步|处理|数据)"
    r"(?:已|已经)?(?:完成|结束|同步|处理)(?:了|啊|呢|呀|啦)?"
    r"(?:(?:并|且)(?:已|已经)?(?:返回|拿到)(?:结果|数据)?)?"
    r"|"
    r"(?:结果|数据)(?:已|已经)?(?:返回|拿到)(?:结果|数据)?(?:了|啊|呢|呀|啦)?"
    r"|"
    r"(?:可以|可|请)?继续(?:下一步)?"
    r")",
    re.IGNORECASE,
)
_OPERATIONAL_STATUS_RE = re.compile(
    r"(?:系统|服务|工具|查询|数据|任务|连接|接口).{0,8}"
    r"(?:正常|运行平稳|运行正常|状态正常|(?:已)?(?:完成|同步|成功|失败|异常)|"
    r"(?:执行|处理|同步)(?:完成|成功|结束))",
    re.IGNORECASE,
)
_OPERATIONAL_SUMMARY_CLAUSE_RE = re.compile(
    r"(?:"
    r"(?:工具|接口|任务|数据|结果)?"
    r"(?:调用|查询|检索|搜索|读取|检查|执行|同步|处理)"
    r"(?:工具|接口|任务|数据|结果)?(?:已|已经)?"
    r"(?:完成|结束|同步|处理)(?:了|啊|呢|呀|啦){0,3}"
    r"|"
    r"(?:已|已经|刚刚)?(?:成功|完成)?(?:调用|查询|检索|搜索|读取|检查|执行|同步|处理){1,2}"
    r"(?:工具|接口|任务|数据|结果)?(?:已|已经)?(?:完成|成功|结束|同步|处理|结果)?"
    r"(?:了|啊|呢|呀|啦)?"
    r"|"
    r"(?:已|已经|刚刚)?(?:成功|完成)?(?:调用|查询|检索|搜索|读取|检查|执行|同步|处理)"
    r"(?:工具|接口|任务|数据|结果)(?:调用|查询|检索|搜索|读取|检查|执行|同步|处理)"
    r"(?:已|已经)?(?:完成|成功|结束|同步|处理|结果)?(?:了|啊|呢|呀|啦)?"
    r"|"
    r"(?:查询|检索|搜索|读取|执行|同步|处理|数据)"
    r"(?:已|已经)?(?:完成|结束|同步|处理)(?:了|啊|呢|呀|啦)?"
    r"(?:(?:并|且)(?:已|已经)?(?:返回|拿到)(?:结果|数据)?)?"
    r"|"
    r"(?:结果|数据)(?:已|已经)?(?:返回|拿到)(?:结果|数据)?(?:了|啊|呢|呀|啦)?"
    r"|"
    r"(?:可以|可|请)?继续(?:下一步)?"
    r")$",
    re.IGNORECASE,
)
_SUMMARY_COMPLETION_RE = re.compile(r"(?:完成|结束|同步|处理)")
_DYNAMIC_FACT_RE = re.compile(
    r"(?:当前|目前|现在|最近|最新|实时|今天|今日|本周|本月|今年).{0,40}(?:是|为|(?<!没)有|达到|排名|最好|最高|最低|正常|异常|运行|发生)",
    re.IGNORECASE,
)
_DYNAMIC_INTERROGATIVE_RE = re.compile(
    r"(?:什么|哪些|多少|如何|怎么|是否|有没有|能否|可否|吗|呢|"
    r"可以帮|需要我(?:帮|协助))",
    re.IGNORECASE,
)
_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?；;，,\n]+")
_GENERIC_FACT_ASSERTION_RE = re.compile(
    r"(?:作者|负责人|创建者|所有者)(?:是|为)|"
    r"(?:该|这|此|其|本|上述|以下)[^。！？\n]{0,24}"
    r"(?:是|为|成立于|创建于|发布于|生效于|位于|属于|包含|支持|要求|规定|明确|显示|表明)|"
    r"(?:成立于|创建于|发布于|生效于)",
    re.IGNORECASE,
)
_INTERNAL_BUSINESS_FACT_RE = re.compile(
    r"(?:排名|业绩|销售额|订单数|总金额|业务员|客户数|同比|环比|数据集|数据表|chatbi)",
    re.IGNORECASE,
)
_RUNTIME_STATE_FACT_RE = re.compile(
    r"(?:cpu|memor|内存|磁盘|disk|负载|load\s*avg|uptime|"
    r"进程|process|端口|port\b|pid\b|filesystem|mounted\s+on|"
    r"\d+\s*(?:gi|mi|ki)b|使用率|占用率|capacity|available)",
    re.IGNORECASE,
)
_PUBLIC_WEB_FACT_RE = re.compile(
    r"(?:天气|新闻|股价|汇率|官网|热搜|头条|公司|企业|成立于)",
    re.IGNORECASE,
)
_USER_FILE_FACT_RE = re.compile(
    r"(?:文件|附件|文档|readme|日志文件|工作区文件)",
    re.IGNORECASE,
)
_KNOWLEDGE_FACT_RE = re.compile(
    r"(?:制度|规范|sop|手册|知识库|运维文档|操作指引)",
    re.IGNORECASE,
)
_MEMORY_FACT_RE = re.compile(
    r"(?:记忆|记录|历史记录|长期记忆|短期记忆|对话记录|上次|之前说过|您曾经|您提过|您说过)",
    re.IGNORECASE,
)
_COMPLETE_RESULT_CLAIM_RE = re.compile(
    r"(?:全部|所有|总数|合计|共计|完整|每一(?:条|项|个)|全部记录|完整列表|一共)",
    re.IGNORECASE,
)

_REFUSAL_MARKERS = (
    "无法",
    "不能确认",
    "暂时不能",
    "暂时无法",
    "没有读取",
    "未读取",
    "没有查询",
    "未查询",
    "没有检索",
    "未检索",
    "未找到",
    "暂无结果",
    "暂无",
)

_CONCRETE_DETAIL_RE = re.compile(
    r"\b[A-Za-z]+\d+[A-Za-z0-9_.:-]*\b|"
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"\d{1,2}:\d{2}(?::\d{2})?|"
    r"\d[\d,.]*\s*(?:张|座|℃|度|公里|分钟|小时|天|日|月|年)",
    re.IGNORECASE,
)

_INTERNAL_TRUSTED_TYPES = frozenset(
    {
        EvidenceType.INTERNAL_DATA,
        EvidenceType.INTERNAL_KNOWLEDGE,
        EvidenceType.USER_FILE,
    }
)
_EXTERNAL_TOOL_COMPATIBLE_TYPES = frozenset(
    {
        EvidenceType.PUBLIC_WEB,
        EvidenceType.RUNTIME_STATE,
    }
)
_READ_SIDE_EVIDENCE_TYPES = frozenset(
    {
        EvidenceType.EXTERNAL_TOOL,
        EvidenceType.PUBLIC_WEB,
        EvidenceType.RUNTIME_STATE,
        EvidenceType.INTERNAL_DATA,
        EvidenceType.INTERNAL_KNOWLEDGE,
        EvidenceType.USER_FILE,
        EvidenceType.CONVERSATION_MEMORY,
    }
)
_CROSS_DOMAIN_BACKING_TYPES = frozenset(
    {
        EvidenceType.EXTERNAL_TOOL,
        EvidenceType.PUBLIC_WEB,
        EvidenceType.RUNTIME_STATE,
        EvidenceType.CONVERSATION_MEMORY,
    }
)


def _ledger_has_partial_overlap(
    ledger: EvidenceLedger,
    candidate_text: str,
    *,
    evidence_types: frozenset[EvidenceType] | None = None,
    freshness: FactFreshness = FactFreshness.UNKNOWN,
    max_age_seconds: int | None = None,
    require_source_as_of: bool = False,
    allow_reuse: bool = False,
) -> bool:
    search_types = evidence_types or ledger.available_evidence_types
    if not search_types:
        return False
    return ledger.has_candidate_overlap(
        candidate_text,
        search_types,
        allow_empty=False,
        freshness=freshness,
        max_age_seconds=max_age_seconds,
        require_source_as_of=require_source_as_of,
        allow_reuse=allow_reuse,
    )


def _requires_internal_trusted_backing(
    *,
    requirement: FactRequirement,
    candidate_text: str,
) -> bool:
    if requirement.accepted_types & _INTERNAL_TRUSTED_TYPES:
        return True
    return _looks_like_internal_business_fact(candidate_text)


def _is_cross_domain_high_risk_warning(
    *,
    requirement: FactRequirement,
    candidate_text: str,
    available_types: frozenset[EvidenceType],
    ledger: EvidenceLedger,
) -> bool:
    """Unrelated read-side families must not soften internal business facts."""
    if not available_types or available_types & _INTERNAL_TRUSTED_TYPES:
        return False
    if not _requires_internal_trusted_backing(
        requirement=requirement,
        candidate_text=candidate_text,
    ):
        return False
    if not (available_types & _CROSS_DOMAIN_BACKING_TYPES):
        return False
    return not _ledger_has_partial_overlap(
        ledger,
        candidate_text,
        evidence_types=available_types & _READ_SIDE_EVIDENCE_TYPES,
        freshness=requirement.freshness,
        max_age_seconds=requirement.max_age_seconds,
        require_source_as_of=requirement.requires_source_timestamp,
        allow_reuse=requirement.allow_conversation_reuse,
    )


def resolve_soft_warning_risk_level(
    *,
    requirement: FactRequirement,
    candidate_text: str,
    ledger: EvidenceLedger,
    reason: str = "",
    force_high: bool = False,
) -> GroundingRiskLevel:
    """Map PASS_WITH_WARNING cases to low / medium / high notice tiers."""
    if force_high:
        return GroundingRiskLevel.HIGH

    available_types = ledger.available_evidence_types
    if not available_types:
        return GroundingRiskLevel.HIGH

    if _is_cross_domain_high_risk_warning(
        requirement=requirement,
        candidate_text=candidate_text,
        available_types=available_types,
        ledger=ledger,
    ):
        return GroundingRiskLevel.HIGH

    overlap_types = requirement.accepted_types or available_types
    if _ledger_has_partial_overlap(
        ledger,
        candidate_text,
        evidence_types=overlap_types & _READ_SIDE_EVIDENCE_TYPES,
        freshness=requirement.freshness,
        max_age_seconds=requirement.max_age_seconds,
        require_source_as_of=requirement.requires_source_timestamp,
        allow_reuse=requirement.allow_conversation_reuse,
    ):
        return GroundingRiskLevel.MEDIUM

    if _ledger_has_partial_overlap(
        ledger,
        candidate_text,
        evidence_types=available_types & _READ_SIDE_EVIDENCE_TYPES,
        freshness=requirement.freshness,
        max_age_seconds=requirement.max_age_seconds,
        require_source_as_of=requirement.requires_source_timestamp,
        allow_reuse=requirement.allow_conversation_reuse,
    ):
        return GroundingRiskLevel.MEDIUM

    if "stale" in str(reason or "").lower() and _ledger_has_partial_overlap(
        ledger,
        candidate_text,
        evidence_types=overlap_types & _READ_SIDE_EVIDENCE_TYPES,
        freshness=requirement.freshness,
        max_age_seconds=requirement.max_age_seconds,
        require_source_as_of=requirement.requires_source_timestamp,
        allow_reuse=requirement.allow_conversation_reuse,
    ):
        return GroundingRiskLevel.MEDIUM

    return GroundingRiskLevel.HIGH


def _evidence_conflict_name(evidence_type: EvidenceType) -> str:
    return {
        EvidenceType.INTERNAL_DATA: "internal_data",
        EvidenceType.INTERNAL_KNOWLEDGE: "internal_knowledge",
        EvidenceType.PUBLIC_WEB: "public_web",
        EvidenceType.RUNTIME_STATE: "runtime_state",
        EvidenceType.USER_FILE: "user_file",
    }.get(evidence_type, evidence_type.value)


def _semantic_evidence_types(decision: RequestDecision) -> frozenset[EvidenceType]:
    domain = str(getattr(decision, "semantic_domain", "") or "").strip().lower()
    fact_kind = str(getattr(decision, "fact_kind", "") or "").strip().lower()
    domain_evidence_types = {
        "chatbi_business_data": frozenset({EvidenceType.INTERNAL_DATA}),
        "internal_docs": frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
        "public_web": frozenset({EvidenceType.PUBLIC_WEB}),
        "runtime_environment": frozenset({EvidenceType.RUNTIME_STATE}),
        "local_file": frozenset({EvidenceType.USER_FILE}),
    }
    fact_kind_evidence_types = {
        "business_metric": frozenset({EvidenceType.INTERNAL_DATA}),
        "runtime_state": frozenset({EvidenceType.RUNTIME_STATE}),
        "machine_load": frozenset({EvidenceType.RUNTIME_STATE}),
        "local_file": frozenset({EvidenceType.USER_FILE}),
        "file_count": frozenset({EvidenceType.USER_FILE}),
        "public_fact": frozenset({EvidenceType.PUBLIC_WEB}),
        "knowledge_document": frozenset({EvidenceType.INTERNAL_KNOWLEDGE}),
    }
    return (
        domain_evidence_types.get(domain)
        or fact_kind_evidence_types.get(fact_kind)
        or frozenset()
    )


def _evidence_contract_for_decision(
    decision: RequestDecision | None,
) -> EvidenceContract:
    if decision is None:
        return EvidenceContract(
            mode=EvidenceContractMode.NONE,
            origin=EvidenceDecisionOrigin.FALLBACK,
            reason="request decision is unavailable",
        )

    source_types = _SOURCE_EVIDENCE_TYPES.get(decision.source, frozenset())
    semantic_types = _semantic_evidence_types(decision)
    conflicts = tuple(
        f"{decision.source.value}_source_with_{_evidence_conflict_name(evidence_type)}_evidence"
        for evidence_type in sorted(
            semantic_types - source_types,
            key=lambda item: item.value,
        )
    )

    if decision.source is RequestSource.UNKNOWN:
        semantic_intent = str(getattr(decision, "semantic_intent", "") or "").strip().upper()
        semantic_confidence = float(getattr(decision, "semantic_confidence", 0.0) or 0.0)
        if semantic_confidence >= 0.7 and semantic_intent in {
            "DATA_QUERY",
            "KNOWLEDGE_BASE",
        }:
            accepted_types = (
                frozenset({EvidenceType.INTERNAL_DATA})
                if semantic_intent == "DATA_QUERY"
                else frozenset({EvidenceType.INTERNAL_KNOWLEDGE})
            )
            return EvidenceContract(
                mode=EvidenceContractMode.REQUIRED,
                accepted_types=accepted_types,
                origin=EvidenceDecisionOrigin.SEMANTIC,
                confidence=semantic_confidence,
                reason="high-confidence semantic intent requires matching evidence",
                conflicts=conflicts,
            )
        return EvidenceContract(
            mode=EvidenceContractMode.OPTIONAL,
            origin=EvidenceDecisionOrigin.FALLBACK,
            confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            reason="unknown source does not establish a mandatory evidence requirement",
            conflicts=conflicts,
        )

    if decision.source in {
        RequestSource.GENERAL,
        RequestSource.PLATFORM_SELF_HELP,
        RequestSource.CONVERSATION_CONTEXT,
    }:
        return EvidenceContract(
            mode=EvidenceContractMode.NONE,
            origin=EvidenceDecisionOrigin.ROUTER,
            confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
            reason="source does not require a new evidence receipt",
            conflicts=conflicts,
        )

    accepted_types = source_types or semantic_types
    return EvidenceContract(
        mode=(
            EvidenceContractMode.REQUIRED
            if accepted_types
            else EvidenceContractMode.NONE
        ),
        accepted_types=accepted_types,
        origin=EvidenceDecisionOrigin.ROUTER,
        confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
        reason=(
            "request source requires matching evidence"
            if accepted_types
            else "request source has no evidence mapping"
        ),
        conflicts=conflicts,
    )


def resolve_fact_requirement(decision: RequestDecision | None) -> FactRequirement:
    contract = _evidence_contract_for_decision(decision)
    if decision is None:
        return FactRequirement(
            required=False,
            accepted_types=frozenset(),
            scrutinize_unknown_output=False,
            evidence_mode=contract.mode.value,
            decision_origin=contract.origin.value,
            decision_confidence=contract.confidence,
            decision_conflicts=contract.conflicts,
        )
    accepted_types = contract.accepted_types
    domain = str(getattr(decision, "semantic_domain", "") or "").strip().lower()
    raw_freshness = str(
        getattr(decision, "freshness_requirement", "unknown") or "unknown"
    ).strip().lower()
    try:
        freshness = FactFreshness(raw_freshness)
    except ValueError:
        freshness = FactFreshness.UNKNOWN
    if freshness is FactFreshness.UNKNOWN:
        if domain == "runtime_environment" or decision.source == RequestSource.RUNTIME_DIAGNOSTIC:
            freshness = FactFreshness.REALTIME
        elif domain in {
            "chatbi_business_data",
            "local_file",
            "public_web",
            "internal_docs",
        }:
            freshness = FactFreshness.DYNAMIC
        elif decision.source == RequestSource.CONVERSATION_CONTEXT:
            freshness = FactFreshness.REUSE_PREVIOUS
    reference_mode = str(
        getattr(decision, "reference_mode", "unknown") or "unknown"
    ).strip().lower()
    needs_fresh_data = bool(getattr(decision, "needs_fresh_data", False))
    if reference_mode in {"reuse_previous", "context_action"}:
        freshness = FactFreshness.REUSE_PREVIOUS
    elif needs_fresh_data and freshness is FactFreshness.REUSE_PREVIOUS:
        # A contradictory legacy payload must fail closed: the explicit
        # current-turn requirement wins over a stale reuse label.
        freshness = FactFreshness.DYNAMIC
    max_age_seconds = getattr(decision, "max_age_seconds", None)
    if max_age_seconds is None and freshness is FactFreshness.REALTIME:
        max_age_seconds = 30
    allow_conversation_reuse = (
        reference_mode in {"reuse_previous", "context_action"}
        or freshness is FactFreshness.REUSE_PREVIOUS
    ) and not needs_fresh_data
    return FactRequirement(
        required=contract.mode is EvidenceContractMode.REQUIRED,
        accepted_types=accepted_types,
        scrutinize_unknown_output=decision.source is RequestSource.UNKNOWN,
        freshness=freshness,
        max_age_seconds=max_age_seconds,
        requires_source_timestamp=bool(
            getattr(decision, "requires_source_timestamp", False)
        ),
        allow_conversation_reuse=allow_conversation_reuse,
        time_scope=getattr(decision, "time_scope", None),
        block_unsupported_facts=needs_fresh_data and bool(accepted_types),
        evidence_mode=contract.mode.value,
        decision_origin=contract.origin.value,
        decision_confidence=contract.confidence,
        decision_conflicts=contract.conflicts,
    )


def evidence_types_for_capabilities(capabilities: object) -> frozenset[EvidenceType]:
    if not isinstance(capabilities, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        evidence_type
        for capability in capabilities
        if (evidence_type := _CAPABILITY_EVIDENCE_TYPES.get(str(capability or "").strip().lower()))
        is not None
    )


def _is_explicitly_hypothetical(text: str) -> bool:
    return any(marker in text for marker in _HYPOTHETICAL_MARKERS)


def _is_explicitly_unverified(text: str) -> bool:
    return any(marker in text for marker in _REFUSAL_MARKERS)


def _contains_dynamic_fact_assertion(text: str) -> bool:
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if not clause or not _DYNAMIC_FACT_RE.search(clause):
            continue
        if _DYNAMIC_INTERROGATIVE_RE.search(clause):
            continue
        return True
    return False


def _contains_structural_external_fact(text: str) -> bool:
    if _is_explicitly_hypothetical(text):
        return False
    has_table = bool(_MARKDOWN_TABLE_SEPARATOR_RE.search(text))
    has_fact_value = bool(_FACT_VALUE_RE.search(text))
    has_numeric_table_value = has_table and bool(re.search(r"\d", text))
    has_execution_claim = bool(_EXECUTION_CLAIM_RE.search(text))
    has_operational_status = bool(_OPERATIONAL_STATUS_RE.search(text))
    has_dynamic_fact = _contains_dynamic_fact_assertion(text)
    has_generic_assertion = bool(_GENERIC_FACT_ASSERTION_RE.search(text))
    return (
        has_execution_claim
        or has_operational_status
        or has_dynamic_fact
        or has_generic_assertion
        or has_fact_value
        or bool(_CONCRETE_DETAIL_RE.search(text))
        or has_numeric_table_value
        or (has_table and has_fact_value)
    )


def _is_non_concrete_execution_summary(text: str) -> bool:
    """允许有成功收据支撑的执行状态总结，不扩大到具体业务事实。"""
    normalized_clauses = [
        re.sub(r"^(?:本轮|本次|此次|当前)(?:的)?", "", clause).strip()
        for clause in _CLAUSE_SPLIT_RE.split(text)
        if clause.strip()
    ]
    normalized_text = "，".join(normalized_clauses)
    has_execution_summary_shape = bool(_EXECUTION_SUMMARY_RE.search(normalized_text))
    if not has_execution_summary_shape and not (
        normalized_clauses
        and all(
            _OPERATIONAL_SUMMARY_CLAUSE_RE.fullmatch(clause)
            for clause in normalized_clauses
        )
        and _SUMMARY_COMPLETION_RE.search(normalized_text)
    ):
        return False
    # “查询已成功/数据已成功”只是一个不完整状态短语，不能因为有成功收据
    # 就被当作纯过程总结放行；至少要出现完成、结束、同步或处理语义。
    if not _SUMMARY_COMPLETION_RE.search(normalized_text):
        return False
    if not normalized_clauses or not all(
        _OPERATIONAL_SUMMARY_CLAUSE_RE.fullmatch(clause)
        for clause in normalized_clauses
    ):
        return False
    return not any(
        (
            _FACT_VALUE_RE.search(text),
            _CONCRETE_DETAIL_RE.search(text),
            _MARKDOWN_TABLE_SEPARATOR_RE.search(text),
            _DYNAMIC_FACT_RE.search(text),
            _GENERIC_FACT_ASSERTION_RE.search(text),
        )
    )


def contains_grounding_fact_signal(text: str) -> bool:
    """Public, conservative fact-signal check shared by runner boundaries."""
    return _contains_structural_external_fact(str(text or "").strip())


def is_non_concrete_execution_summary(text: str) -> bool:
    """Return whether text is only an operational completion summary."""
    return _is_non_concrete_execution_summary(str(text or "").strip())


def requires_complete_result_evidence(text: str) -> bool:
    """判断回答是否声称结果覆盖全集，而非仅陈述已返回的部分结果。"""

    return bool(_COMPLETE_RESULT_CLAIM_RE.search(str(text or "")))


def _is_pure_no_result_response(text: str) -> bool:
    return bool(
        _is_explicitly_unverified(text)
        and not _FACT_VALUE_RE.search(text)
        and not _CONCRETE_DETAIL_RE.search(text)
        and not _MARKDOWN_TABLE_SEPARATOR_RE.search(text)
    )


def _looks_like_internal_business_fact(text: str) -> bool:
    if _INTERNAL_BUSINESS_FACT_RE.search(text):
        return True
    has_table = bool(_MARKDOWN_TABLE_SEPARATOR_RE.search(text))
    return has_table and bool(re.search(r"[¥￥万]", text))


def _infer_acceptable_evidence_for_structural_fact(text: str) -> frozenset[EvidenceType]:
    """Best-effort map from candidate answer text to acceptable evidence types."""
    acceptable: set[EvidenceType] = set()
    if _looks_like_internal_business_fact(text):
        acceptable.add(EvidenceType.INTERNAL_DATA)
    if _RUNTIME_STATE_FACT_RE.search(text):
        acceptable.add(EvidenceType.RUNTIME_STATE)
    if _PUBLIC_WEB_FACT_RE.search(text):
        acceptable.add(EvidenceType.PUBLIC_WEB)
    if _USER_FILE_FACT_RE.search(text):
        acceptable.add(EvidenceType.USER_FILE)
    if _KNOWLEDGE_FACT_RE.search(text):
        acceptable.add(EvidenceType.INTERNAL_KNOWLEDGE)
    if _MEMORY_FACT_RE.search(text):
        acceptable.add(EvidenceType.CONVERSATION_MEMORY)
    return frozenset(acceptable)


def _infer_evidence_requirement_groups(text: str) -> tuple[frozenset[EvidenceType], ...]:
    groups: list[frozenset[EvidenceType]] = []
    for claim in re.split(r"[。！？\n]+", text):
        claim = claim.strip()
        if not claim:
            continue
        acceptable = _infer_acceptable_evidence_for_structural_fact(claim)
        if acceptable:
            groups.append(acceptable)
    return tuple(groups)


def evaluate_grounding(
    *,
    requirement: FactRequirement,
    candidate_text: str,
    ledger: EvidenceLedger,
) -> GroundingDecision:
    text = str(candidate_text or "").strip()
    available_types = ledger.available_evidence_types
    fact_bearing = _contains_structural_external_fact(text)
    allow_truncated = bool(
        requirement.allow_truncated and not requires_complete_result_evidence(text)
    )
    if requirement.block_unsupported_facts and not fact_bearing:
        return GroundingDecision(
            GroundingAction.PASS,
            "fresh-data response contains no independently verifiable factual claim",
            requirement.accepted_types,
            available_types,
        )
    exact_evidence_exists = bool(
        requirement.accepted_types
        and ledger.has_fresh_evidence(
            requirement.accepted_types,
            freshness=requirement.freshness,
            max_age_seconds=requirement.max_age_seconds,
            require_source_as_of=requirement.requires_source_timestamp,
            allow_reuse=requirement.allow_conversation_reuse,
            allow_truncated=allow_truncated,
        )
    )
    stale_exact_evidence_exists = bool(
        requirement.accepted_types
        and ledger.has_valid_evidence(
            requirement.accepted_types,
            allow_truncated=allow_truncated,
        )
        and not exact_evidence_exists
    )
    if exact_evidence_exists:
        if _is_non_concrete_execution_summary(text):
            return GroundingDecision(
                GroundingAction.PASS,
                "successful evidence receipt supports a non-concrete execution summary",
                requirement.accepted_types,
                available_types,
            )
        receipt_correlated = ledger.has_candidate_overlap(
            text,
            requirement.accepted_types,
            allow_empty=_is_pure_no_result_response(text),
            freshness=requirement.freshness,
            max_age_seconds=requirement.max_age_seconds,
            require_source_as_of=requirement.requires_source_timestamp,
            allow_reuse=requirement.allow_conversation_reuse,
            allow_truncated=allow_truncated,
        )
        if not fact_bearing or receipt_correlated:
            return GroundingDecision(
                GroundingAction.PASS,
                "matching evidence receipt exists",
                requirement.accepted_types,
                available_types,
            )
        if requirement.block_unsupported_facts:
            return GroundingDecision(
                GroundingAction.PASS_WITH_WARNING,
                "fresh-data request is backed only by an empty or unrelated result; response retained with a high-risk warning",
                requirement.accepted_types,
                available_types,
                GroundingRiskLevel.HIGH,
            )

    if requirement.required:
        if stale_exact_evidence_exists:
            if requirement.block_unsupported_facts:
                return GroundingDecision(
                    GroundingAction.PASS_WITH_WARNING,
                    "fresh-data request has only stale evidence for a concrete fact; response retained with a high-risk warning",
                    requirement.accepted_types,
                    available_types,
                    GroundingRiskLevel.HIGH,
                )
            return GroundingDecision(
                GroundingAction.PASS_WITH_WARNING,
                "required evidence is stale or does not satisfy the freshness requirement",
                requirement.accepted_types,
                available_types,
                resolve_soft_warning_risk_level(
                    requirement=requirement,
                    candidate_text=text,
                    ledger=ledger,
                    reason="stale evidence",
                ),
            )
        if (
            EvidenceType.EXTERNAL_TOOL in available_types
            and requirement.accepted_types
            and requirement.accepted_types <= _EXTERNAL_TOOL_COMPATIBLE_TYPES
            and ledger.has_candidate_overlap(
                text,
                {EvidenceType.EXTERNAL_TOOL},
                allow_empty=_is_pure_no_result_response(text),
                freshness=requirement.freshness,
                max_age_seconds=requirement.max_age_seconds,
                require_source_as_of=requirement.requires_source_timestamp,
                allow_reuse=requirement.allow_conversation_reuse,
                allow_truncated=allow_truncated,
            )
        ):
            return GroundingDecision(
                GroundingAction.PASS,
                "external or runtime request backed by a successful external tool result",
                requirement.accepted_types,
                available_types,
            )
        if (
            _is_pure_no_result_response(text)
        ):
            return GroundingDecision(
                GroundingAction.PASS,
                "response explicitly avoids unverified factual claims",
                requirement.accepted_types,
                available_types,
            )
        if exact_evidence_exists:
            return GroundingDecision(
                GroundingAction.PASS_WITH_WARNING,
                "matching evidence type exists but the answer is not correlated with its content",
                requirement.accepted_types,
                available_types,
                resolve_soft_warning_risk_level(
                    requirement=requirement,
                    candidate_text=text,
                    ledger=ledger,
                    reason="uncorrelated exact evidence",
                ),
            )
        internal_requirement = bool(requirement.accepted_types & _INTERNAL_TRUSTED_TYPES)
        internal_evidence = bool(available_types & _INTERNAL_TRUSTED_TYPES)
        if internal_requirement and internal_evidence:
            return GroundingDecision(
                GroundingAction.PASS_WITH_WARNING,
                "compatible internal evidence exists but the source type is not an exact match",
                requirement.accepted_types,
                available_types,
                (
                    GroundingRiskLevel.HIGH
                    if requirement.block_unsupported_facts
                    else GroundingRiskLevel.LOW
                ),
            )
        return GroundingDecision(
            GroundingAction.PASS_WITH_WARNING,
            "required evidence receipt is missing",
            requirement.accepted_types,
            available_types,
            GroundingRiskLevel.HIGH
            if requirement.block_unsupported_facts
            else resolve_soft_warning_risk_level(
                requirement=requirement,
                candidate_text=text,
                ledger=ledger,
                reason="required evidence receipt is missing",
            ),
        )

    if requirement.scrutinize_unknown_output:
        if _contains_structural_external_fact(text):
            if (
                EvidenceType.EXTERNAL_TOOL in available_types
                and not _looks_like_internal_business_fact(text)
                and ledger.has_candidate_overlap(
                    text,
                    {EvidenceType.EXTERNAL_TOOL},
                    allow_empty=_is_pure_no_result_response(text),
                    freshness=requirement.freshness,
                    max_age_seconds=requirement.max_age_seconds,
                    require_source_as_of=requirement.requires_source_timestamp,
                    allow_reuse=requirement.allow_conversation_reuse,
                    allow_truncated=allow_truncated,
                )
            ):
                return GroundingDecision(
                    GroundingAction.PASS,
                    "unknown request backed by a successful external tool result",
                    available_evidence_types=available_types,
                )
            requirement_groups = _infer_evidence_requirement_groups(text)
            if requirement_groups and all(
                ledger.has_candidate_overlap(
                    text,
                    alternatives,
                    freshness=requirement.freshness,
                    max_age_seconds=requirement.max_age_seconds,
                    require_source_as_of=requirement.requires_source_timestamp,
                    allow_reuse=requirement.allow_conversation_reuse,
                    allow_truncated=allow_truncated,
                )
                for alternatives in requirement_groups
            ):
                return GroundingDecision(
                    GroundingAction.PASS,
                    "unknown request backed by matching tool evidence",
                    available_evidence_types=available_types,
                )
            missing_groups = tuple(
                alternatives
                for alternatives in requirement_groups
                if not ledger.has_candidate_overlap(
                    text,
                    alternatives,
                    freshness=requirement.freshness,
                    max_age_seconds=requirement.max_age_seconds,
                    require_source_as_of=requirement.requires_source_timestamp,
                    allow_reuse=requirement.allow_conversation_reuse,
                    allow_truncated=allow_truncated,
                )
            )
            missing_types = frozenset(
                evidence_type
                for alternatives in missing_groups
                for evidence_type in alternatives
            )
            return GroundingDecision(
                GroundingAction.PASS_WITH_WARNING,
                "unknown request emitted a dynamic or structured fact without matched evidence",
                missing_types,
                available_types,
                resolve_soft_warning_risk_level(
                    requirement=requirement,
                    candidate_text=text,
                    ledger=ledger,
                    reason="unknown structured fact without matched evidence",
                ),
            )
        return GroundingDecision(
            GroundingAction.PASS,
            "unknown output has no external fact signal",
            available_evidence_types=available_types,
        )

    return GroundingDecision(
        GroundingAction.PASS,
        "no external evidence requirement",
        available_evidence_types=available_types,
    )
