"""Unified grounding audit facade shared by runner boundaries."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.policy import (
    FactRequirement,
    GroundingAction,
    GroundingDecision,
    GroundingRiskLevel,
    evaluate_grounding,
)


@dataclass(frozen=True)
class GroundingAuditResult:
    """A policy decision plus the optional user-visible soft warning."""

    decision: GroundingDecision
    warning_chunk: dict[str, object] | None = None

    @property
    def should_warn(self) -> bool:
        return self.warning_chunk is not None


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
        if risk_level == GroundingRiskLevel.LOW:
            notice = (
                "> **信息来源提示**：本回答基于知识库或已授权文件资料，"
                "不代表实时数据库状态。"
            )
        elif risk_level == GroundingRiskLevel.MEDIUM:
            notice = (
                "> **信息来源提示**：本回答参考了已取得的资料，但部分结论未获得"
                "完全匹配的数据来源，请结合原始资料核对。"
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
                "required_evidence_types": sorted(
                    item.value for item in required_types
                ),
                "available_evidence_types": sorted(
                    item.value for item in available_types
                ),
            },
        }
