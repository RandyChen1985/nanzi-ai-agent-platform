from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.request_decision import RequestDecision, RequestSource


class GroundingAction(str, Enum):
    PASS = "pass"
    PASS_WITHOUT_FACTS = "pass_without_facts"
    BLOCK_UNGROUNDED_FACTS = "block_ungrounded_facts"


@dataclass(frozen=True)
class FactRequirement:
    required: bool
    accepted_types: frozenset[EvidenceType]
    scrutinize_unknown_output: bool = False


@dataclass(frozen=True)
class GroundingDecision:
    action: GroundingAction
    reason: str
    required_evidence_types: frozenset[EvidenceType] = frozenset()


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
_DYNAMIC_FACT_RE = re.compile(
    r"(?:当前|目前|现在|最近|最新|实时|今天|今日|本周|本月|今年).{0,40}(?:是|为|有|达到|排名|最好|最高|最低|正常|异常|运行|发生)",
    re.IGNORECASE,
)
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


def resolve_fact_requirement(decision: RequestDecision | None) -> FactRequirement:
    if decision is None:
        return FactRequirement(False, frozenset(), scrutinize_unknown_output=True)
    accepted_types = _SOURCE_EVIDENCE_TYPES.get(decision.source, frozenset())
    return FactRequirement(
        required=bool(accepted_types),
        accepted_types=accepted_types,
        scrutinize_unknown_output=False,
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


def _contains_structural_external_fact(text: str) -> bool:
    if _is_explicitly_hypothetical(text):
        return False
    has_table = bool(_MARKDOWN_TABLE_SEPARATOR_RE.search(text))
    has_fact_value = bool(_FACT_VALUE_RE.search(text))
    has_numeric_table_value = has_table and bool(re.search(r"\d", text))
    has_execution_claim = bool(_EXECUTION_CLAIM_RE.search(text))
    has_dynamic_fact = bool(_DYNAMIC_FACT_RE.search(text))
    has_generic_assertion = bool(_GENERIC_FACT_ASSERTION_RE.search(text))
    return (
        has_execution_claim
        or has_dynamic_fact
        or has_generic_assertion
        or has_numeric_table_value
        or (has_table and has_fact_value)
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
    if requirement.accepted_types and ledger.has_valid_evidence(requirement.accepted_types):
        return GroundingDecision(GroundingAction.PASS, "matching evidence receipt exists")

    if requirement.required:
        return GroundingDecision(
            GroundingAction.BLOCK_UNGROUNDED_FACTS,
            "required evidence receipt is missing",
            requirement.accepted_types,
        )

    if requirement.scrutinize_unknown_output:
        if _contains_structural_external_fact(text):
            requirement_groups = _infer_evidence_requirement_groups(text)
            if requirement_groups and all(
                ledger.has_valid_evidence(alternatives)
                for alternatives in requirement_groups
            ):
                return GroundingDecision(
                    GroundingAction.PASS,
                    "unknown request backed by matching tool evidence",
                )
            missing_groups = tuple(
                alternatives
                for alternatives in requirement_groups
                if not ledger.has_valid_evidence(alternatives)
            )
            missing_types = frozenset(
                evidence_type
                for alternatives in missing_groups
                for evidence_type in alternatives
            )
            return GroundingDecision(
                GroundingAction.BLOCK_UNGROUNDED_FACTS,
                "unknown request emitted a dynamic or structured fact without matched evidence",
                missing_types,
            )
        return GroundingDecision(GroundingAction.PASS, "unknown output has no external fact signal")

    return GroundingDecision(GroundingAction.PASS, "no external evidence requirement")
