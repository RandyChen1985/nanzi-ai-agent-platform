import pytest

from app.services.ai.grounding.models import EvidenceReceipt, EvidenceType, FactFreshness


pytestmark = pytest.mark.no_infrastructure


def test_fact_freshness_has_explicit_compatibility_values():
    assert FactFreshness.STATIC.value == "static"
    assert FactFreshness.REALTIME.value == "realtime"
    assert FactFreshness.REUSE_PREVIOUS.value == "reuse_previous"


def test_evidence_receipt_defaults_to_unknown_freshness():
    receipt = EvidenceReceipt.create(
        call_id="call-1",
        producer="tool",
        evidence_types=frozenset({EvidenceType.PUBLIC_WEB}),
        payload_digest="digest",
        user_id="u1",
        conversation_id="c1",
    )

    assert receipt.freshness is FactFreshness.UNKNOWN
    assert receipt.observed_at == receipt.created_at
    assert receipt.source_as_of is None
    assert receipt.expires_at is None
    assert receipt.source_ref is None
