from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class EvidenceType(str, Enum):
    INTERNAL_DATA = "internal_data"
    INTERNAL_KNOWLEDGE = "internal_knowledge"
    PUBLIC_WEB = "public_web"
    RUNTIME_STATE = "runtime_state"
    USER_FILE = "user_file"
    CONVERSATION_MEMORY = "conversation_memory"
    # A successful result returned by a dynamically registered external tool
    # (currently MCP). This proves that the current turn consulted a tool, but
    # intentionally does not impersonate a more specific source such as an
    # internal dataset or knowledge base.
    EXTERNAL_TOOL = "external_tool"


@dataclass(frozen=True)
class EvidenceReceipt:
    call_id: str
    producer: str
    evidence_types: frozenset[EvidenceType]
    payload_digest: str
    user_id: str | None
    conversation_id: str | None
    created_at: datetime
    marker_digests: frozenset[str] = frozenset()
    empty_success: bool = False

    @classmethod
    def create(
        cls,
        *,
        call_id: str,
        producer: str,
        evidence_types: frozenset[EvidenceType],
        payload_digest: str,
        user_id: str | None,
        conversation_id: str | None,
        marker_digests: frozenset[str] = frozenset(),
        empty_success: bool = False,
    ) -> "EvidenceReceipt":
        return cls(
            call_id=call_id,
            producer=producer,
            evidence_types=evidence_types,
            payload_digest=payload_digest,
            user_id=user_id,
            conversation_id=conversation_id,
            created_at=datetime.now(timezone.utc),
            marker_digests=marker_digests,
            empty_success=empty_success,
        )
