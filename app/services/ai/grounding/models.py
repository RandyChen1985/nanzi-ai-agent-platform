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


class FactFreshness(str, Enum):
    """事实证据的时效语义。

    ``UNKNOWN`` 是旧调用方和旧会话的兼容值：它不额外触发时效阻断，
    但仍然遵守原有的证据类型与内容相关性校验。
    """

    STATIC = "static"
    HISTORICAL = "historical"
    DYNAMIC = "dynamic"
    REALTIME = "realtime"
    REUSE_PREVIOUS = "reuse_previous"
    UNKNOWN = "unknown"


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
    strong_marker_digests: frozenset[str] = frozenset()
    empty_success: bool = False
    observed_at: datetime | None = None
    source_as_of: datetime | None = None
    expires_at: datetime | None = None
    freshness: FactFreshness = FactFreshness.UNKNOWN
    source_ref: str | None = None

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
        strong_marker_digests: frozenset[str] = frozenset(),
        empty_success: bool = False,
        observed_at: datetime | None = None,
        source_as_of: datetime | None = None,
        expires_at: datetime | None = None,
        freshness: FactFreshness = FactFreshness.UNKNOWN,
        source_ref: str | None = None,
    ) -> "EvidenceReceipt":
        created_at = datetime.now(timezone.utc)
        return cls(
            call_id=call_id,
            producer=producer,
            evidence_types=evidence_types,
            payload_digest=payload_digest,
            user_id=user_id,
            conversation_id=conversation_id,
            created_at=created_at,
            marker_digests=marker_digests,
            strong_marker_digests=strong_marker_digests,
            empty_success=empty_success,
            observed_at=observed_at or created_at,
            source_as_of=source_as_of,
            expires_at=expires_at,
            freshness=FactFreshness(freshness),
            source_ref=source_ref,
        )
