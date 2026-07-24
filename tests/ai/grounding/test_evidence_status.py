import pytest

from app.services.ai.grounding.ledger import EvidenceLedger, classify_evidence_result
from app.services.ai.grounding.models import EvidenceStatus, EvidenceType


pytestmark = pytest.mark.no_infrastructure


def test_evidence_status_distinguishes_non_empty_empty_and_failed_results():
    assert classify_evidence_result({"success": True, "items": [{"id": 1}]}) is EvidenceStatus.SUCCESS_NON_EMPTY
    assert classify_evidence_result({"success": True, "items": []}) is EvidenceStatus.SUCCESS_EMPTY
    assert classify_evidence_result({"rows": [], "total": 0}) is EvidenceStatus.SUCCESS_EMPTY
    assert classify_evidence_result({"success": False, "message": "SQL 执行失败"}) is EvidenceStatus.FAILED


@pytest.mark.parametrize("field", ["count", "total", "row_count", "affected_rows"])
def test_positive_count_only_success_is_non_empty(field):
    assert classify_evidence_result({"success": True, field: 42}) is EvidenceStatus.SUCCESS_NON_EMPTY


def test_positive_total_proves_data_exists_even_when_current_page_is_empty():
    assert classify_evidence_result({"success": True, "rows": [], "total": 5}) is EvidenceStatus.SUCCESS_NON_EMPTY


def test_zero_count_only_success_is_empty():
    assert classify_evidence_result({"success": True, "count": 0}) is EvidenceStatus.SUCCESS_EMPTY


def test_receipt_persists_explicit_empty_status_through_snapshot():
    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    receipt = ledger.record_success(
        call_id="empty-query",
        producer="execute_sql_query",
        evidence_types={EvidenceType.INTERNAL_DATA},
        result={"success": True, "items": []},
        policy="allow_empty_success",
    )

    assert receipt is not None
    assert receipt.status is EvidenceStatus.SUCCESS_EMPTY

    restored = EvidenceLedger.from_snapshot(
        ledger.to_snapshot(),
        user_id="u1",
        conversation_id="c1",
    )
    assert restored.receipts[0].status is EvidenceStatus.SUCCESS_EMPTY
