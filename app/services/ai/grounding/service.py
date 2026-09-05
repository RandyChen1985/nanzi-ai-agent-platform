"""Unified grounding audit facade shared by runner boundaries."""

from __future__ import annotations

from dataclasses import dataclass
import re

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.policy import (
    FactRequirement,
    GroundingAction,
    GroundingDecision,
    GroundingRiskLevel,
    contains_grounding_fact_signal,
    evaluate_grounding,
)


_EVIDENCE_TYPE_LABELS = {
    EvidenceType.INTERNAL_DATA: "内部数据",
    EvidenceType.INTERNAL_KNOWLEDGE: "知识库资料",
    EvidenceType.PUBLIC_WEB: "公开网络资料",
    EvidenceType.RUNTIME_STATE: "运行状态",
    EvidenceType.USER_FILE: "用户文件",
    EvidenceType.EXTERNAL_TOOL: "外部工具结果",
    EvidenceType.CONVERSATION_MEMORY: "会话记忆",
}

_UNCERTAIN_RESPONSE_RE = re.compile(
    r"(?:无法|不能(?:确认|确定)?|不确定|不足|未找到|没有找到|未获得|未能|暂无|"
    r"没有(?:拿到|获得|返回)(?:有效)?(?:结果|数据|信息)|请(?:提供|补充))",
    re.IGNORECASE,
)


def _format_evidence_type_labels(evidence_types: frozenset[EvidenceType]) -> str:
    labels = [
        _EVIDENCE_TYPE_LABELS.get(item, item.value)
        for item in sorted(evidence_types, key=lambda value: value.value)
    ]
    return "、".join(labels) if labels else "无"


def _humanize_grounding_reason(
    reason: str,
    *,
    required_types: frozenset[EvidenceType],
    available_types: frozenset[EvidenceType],
) -> str:
    """将策略层原因转换为面向用户的说明，同时保留原始 reason 供诊断。"""
    normalized = str(reason or "").strip()
    required = _format_evidence_type_labels(required_types)
    available = _format_evidence_type_labels(available_types)
    if normalized == "required evidence receipt is missing":
        if required_types and not available_types:
            return f"本轮没有找到与回答对应的{required}证据。"
        return f"本轮没有找到与回答对应的证据，需要{required}，当前获得的是{available}。"
    if normalized.lower().startswith("evidence contract missing fresh receipt"):
        return (
            f"本轮没有拿到与当前问题对应的{required}。"
            if required_types
            else "本轮没有拿到与当前问题对应的可核对结果。"
        )
    if "stale evidence" in normalized.lower():
        return f"本轮{required}证据已经过期，不能证明当前状态。"
    if "compatible internal evidence" in normalized.lower():
        return f"本轮获得了{available}，但本次回答需要{required}，来源类型不完全匹配。"
    if "empty or unrelated result" in normalized.lower():
        return f"本轮虽然获得了{available}，但结果为空或与回答内容不对应。"
    if "not correlated" in normalized.lower() or "uncorrelated" in normalized.lower():
        return "本轮证据类型匹配，但回答内容无法与证据结果对应。"
    if any("\u4e00" <= char <= "\u9fff" for char in normalized):
        return normalized
    return "本轮证据校验未完全通过，请结合原始数据核对。"


@dataclass(frozen=True)
class GroundingAuditResult:
    """A policy decision plus the optional user-visible soft warning."""

    decision: GroundingDecision
    warning_chunk: dict[str, object] | None = None

    @property
    def should_warn(self) -> bool:
        return self.warning_chunk is not None


@dataclass(frozen=True)
class GroundingGuidance:
    """面向用户的安全降级回复及其是否保留了原始不确定性说明。"""

    content: str
    retained_candidate: bool


def _is_retainable_uncertainty(text: str) -> bool:
    normalized = str(text or "").strip()
    return bool(
        normalized
        and _UNCERTAIN_RESPONSE_RE.search(normalized)
        and not contains_grounding_fact_signal(normalized)
    )


def _next_step_guidance(
    reason: str,
    required_types: frozenset[EvidenceType],
) -> str:
    lowered = str(reason or "").lower()
    if "stale" in lowered or "过期" in lowered:
        return "重新查询最新结果，或明确你需要的时间范围。"
    if "truncated" in lowered or "截断" in lowered:
        return "缩小查询范围，分批获取结果，这样更容易确认完整信息。"
    if "empty" in lowered or "unrelated" in lowered or "为空" in lowered:
        return "换一个更具体的关键词，或补充项目、对象和时间范围。"
    if EvidenceType.INTERNAL_DATA in required_types:
        return "补充数据集、指标、筛选范围或时间条件。"
    if EvidenceType.INTERNAL_KNOWLEDGE in required_types:
        return "补充制度、手册名称或更具体的业务关键词。"
    if EvidenceType.USER_FILE in required_types:
        return "确认要查询的附件或文件，并补充页码、工作表或关键词。"
    if required_types & {EvidenceType.PUBLIC_WEB, EvidenceType.EXTERNAL_TOOL}:
        return "补充具体名称、账号、地区或时间范围后再试。"
    if EvidenceType.RUNTIME_STATE in required_types:
        return "补充主机、服务或时间范围后再试。"
    return "你可以重新查询一次，也可以补充更具体的名称、范围或时间条件。"


class GroundingService:
    """Evaluate complete candidate text without owning runner orchestration."""

    @staticmethod
    def audit(
        *,
        candidate_text: str,
        requirement: FactRequirement,
        ledger: EvidenceLedger,
        enabled: bool = True,
    ) -> GroundingAuditResult:
        if not enabled:
            requirement = FactRequirement(required=False, accepted_types=frozenset())
        decision = evaluate_grounding(
            requirement=requirement,
            candidate_text=candidate_text,
            ledger=ledger,
        )
        warning_chunk = None
        if decision.action in {
            GroundingAction.PASS_WITH_WARNING,
            GroundingAction.BLOCK_UNGROUNDED_FACTS,
        }:
            warning_chunk = GroundingService.warning_chunk(
                risk_level=(
                    decision.risk_level
                    if decision.risk_level != GroundingRiskLevel.NONE
                    else GroundingRiskLevel.HIGH
                ),
                reason=decision.reason,
                required_types=decision.required_evidence_types,
                available_types=decision.available_evidence_types,
            )
        return GroundingAuditResult(
            decision=decision,
            warning_chunk=warning_chunk,
        )

    @staticmethod
    def warning_chunk(
        *,
        risk_level: GroundingRiskLevel,
        reason: str,
        required_types: frozenset[EvidenceType] = frozenset(),
        available_types: frozenset[EvidenceType] = frozenset(),
    ) -> dict[str, object]:
        user_reason = _humanize_grounding_reason(
            reason,
            required_types=required_types,
            available_types=available_types,
        )
        if risk_level == GroundingRiskLevel.LOW:
            notice = (
                "> **信息来源提示**：本回答基于知识库或已授权文件资料，"
                "不代表实时数据库状态。"
            )
        elif risk_level == GroundingRiskLevel.MEDIUM:
            notice = (
                "> **信息来源提示**：本回答参考了已取得的工具或资料结果，"
                "但部分结论未获得完全匹配的数据来源，请结合原始资料核对。"
            )
        else:
            notice = (
                "> **风险提示**：本回答中的具体数据或实时状态尚未经工具结果完整核实，"
                "可能存在偏差。重要操作或正式决策前，请以原始数据源为准。"
            )
        return {
            "content": f"\n\n{notice}",
            "grounding_risk": {
                "level": risk_level.value,
                "reason": reason,
                "user_reason": user_reason,
                "required_evidence_types": sorted(
                    item.value for item in required_types
                ),
                "available_evidence_types": sorted(
                    item.value for item in available_types
                ),
            },
        }

    @staticmethod
    def guided_response(
        *,
        candidate_text: str,
        reason: str,
        required_types: frozenset[EvidenceType] = frozenset(),
        available_types: frozenset[EvidenceType] = frozenset(),
        contracts_reason: str = "",
    ) -> GroundingGuidance:
        """生成安全降级回复，优先保留模型已经表达的“不确定”说明。

        该方法不会保留带有具体未经核实事实的原文，只保留纯不确定性说明；
        这样可以避免用户被硬阻断，同时不放行数字、状态或业务结论。
        """

        effective_reason = str(contracts_reason or reason or "").strip()
        user_reason = _humanize_grounding_reason(
            effective_reason,
            required_types=required_types,
            available_types=available_types,
        )
        guidance = _next_step_guidance(effective_reason, required_types)
        candidate = str(candidate_text or "").strip()
        if _is_retainable_uncertainty(candidate):
            return GroundingGuidance(
                content=(
                    f"{candidate}\n\n"
                    f"你可以：{guidance}"
                ),
                retained_candidate=True,
            )
        return GroundingGuidance(
            content=(
                "我先不直接给出具体结论，以免把未核实的信息当成事实。\n\n"
                f"{user_reason}\n\n"
                f"你可以：{guidance}"
            ),
            retained_candidate=False,
        )
