import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType, FactFreshness


pytestmark = pytest.mark.no_infrastructure


def test_successful_non_empty_result_creates_scoped_receipt():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")

    receipt = ledger.record_success(
        call_id="call-1",
        producer="knowledge-search",
        evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
        result={"content": "请假制度正文"},
    )

    assert receipt is not None
    assert ledger.has_valid_evidence({EvidenceType.INTERNAL_KNOWLEDGE})
    assert not ledger.has_valid_evidence({EvidenceType.INTERNAL_DATA})
    assert receipt.user_id == "7"
    assert receipt.conversation_id == "conv-1"


def test_typed_runtime_receipt_gets_realtime_freshness_by_default():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")

    receipt = ledger.record_success(
        call_id="runtime-1",
        producer="list_process",
        evidence_types={EvidenceType.RUNTIME_STATE},
        result={"load": 0.2},
    )

    assert receipt is not None
    assert receipt.freshness is FactFreshness.REALTIME


def test_typed_file_receipt_gets_dynamic_freshness_by_default():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")

    receipt = ledger.record_success(
        call_id="file-1",
        producer="read_file",
        evidence_types={EvidenceType.USER_FILE},
        result="file content",
    )

    assert receipt is not None
    assert receipt.freshness is FactFreshness.DYNAMIC


def test_empty_or_error_like_results_do_not_create_receipts():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")

    for result in (
        None,
        "",
        [],
        {},
        "错误：工具调用失败",
        "[TOOL_ERROR] permission denied",
        "[MCP Error] remote server unavailable",
        "[Execution Error] connection failed",
        "[Error] unsupported method",
        "执行成功，但查询结果为空",
        "[SUCCESS] 未找到匹配的 Jira 工单",
        "未找到匹配内容，请调整关键词",
        "未找到匹配的会话摘要",
        "子智能体已执行完成，但未产生可交付正文",
        '{"content": "", "citations": []}',
        {"content": "", "rows": []},
        {"success": False, "message": "business failure"},
        {"isError": True, "content": "remote tool failed"},
        {"is_error": True, "content": "remote tool failed"},
        {"state": "error", "content": "remote tool failed"},
        {"code": 500, "message": "server error"},
        {"status": "error", "content": "failed"},
        SimpleNamespace(state="error", content="command failed"),
        SimpleNamespace(isError=True, content="remote tool failed"),
    ):
        receipt = ledger.record_success(
            call_id="call-empty",
            producer="tool",
            evidence_types={EvidenceType.PUBLIC_WEB},
            result=result,
        )
        assert receipt is None

    assert not ledger.has_valid_evidence({EvidenceType.PUBLIC_WEB})


def test_receipt_requires_a_declared_evidence_type():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")

    receipt = ledger.record_success(
        call_id="call-1",
        producer="unclassified-tool",
        evidence_types=set(),
        result="some output",
    )

    assert receipt is None
    assert ledger.receipts == ()


def test_ledger_snapshot_roundtrip_preserves_scoped_receipts():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-1",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"trains": [{"number": "G1"}]},
    )

    restored = EvidenceLedger.from_snapshot(
        ledger.to_snapshot(),
        user_id="7",
        conversation_id="conv-1",
    )

    assert restored.has_valid_evidence({EvidenceType.EXTERNAL_TOOL})
    assert restored.receipts[0].call_id == "call-1"


def test_ledger_roundtrip_preserves_freshness_metadata():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    observed_at = datetime(2026, 7, 23, 2, 0, tzinfo=timezone.utc)
    source_as_of = datetime(2026, 7, 23, 1, 59, tzinfo=timezone.utc)
    expires_at = datetime(2026, 7, 23, 2, 1, tzinfo=timezone.utc)

    receipt = ledger.record_success(
        call_id="call-1",
        producer="runtime-tool",
        evidence_types={EvidenceType.RUNTIME_STATE},
        result={"load": 0.2},
        observed_at=observed_at,
        source_as_of=source_as_of,
        expires_at=expires_at,
        freshness=FactFreshness.REALTIME,
        source_ref="host://current-machine",
    )

    restored = EvidenceLedger.from_snapshot(
        ledger.to_snapshot(),
        user_id="7",
        conversation_id="conv-1",
    )

    assert receipt is not None
    assert restored.receipts[0].observed_at == observed_at
    assert restored.receipts[0].source_as_of == source_as_of
    assert restored.receipts[0].expires_at == expires_at
    assert restored.receipts[0].freshness is FactFreshness.REALTIME
    assert restored.receipts[0].source_ref == "host://current-machine"


def test_ledger_restores_legacy_snapshot_without_freshness_fields():
    created_at = "2026-07-23T02:00:00+00:00"
    snapshot = [
        {
            "call_id": "call-legacy",
            "producer": "legacy-tool",
            "evidence_types": ["public_web"],
            "payload_digest": "digest",
            "user_id": "7",
            "conversation_id": "conv-1",
            "created_at": created_at,
        }
    ]

    restored = EvidenceLedger.from_snapshot(
        snapshot,
        user_id="7",
        conversation_id="conv-1",
    )

    assert len(restored.receipts) == 1
    assert restored.receipts[0].freshness is FactFreshness.UNKNOWN
    assert restored.receipts[0].observed_at == restored.receipts[0].created_at


def test_ledger_snapshot_rejects_receipt_from_another_scope():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-1",
        producer="tool",
        evidence_types={EvidenceType.PUBLIC_WEB},
        result="ok",
    )

    restored = EvidenceLedger.from_snapshot(
        ledger.to_snapshot(),
        user_id="8",
        conversation_id="conv-2",
    )

    assert restored.receipts == ()


def test_ledger_matches_candidate_against_hashed_result_markers():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-train",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"number": "G1505", "departure": "07:50", "price": 973},
    )

    assert ledger.has_candidate_overlap(
        "G1505 次列车 07:50 出发，二等座 973 元。",
        {EvidenceType.EXTERNAL_TOOL},
    )
    assert not ledger.has_candidate_overlap(
        "上海明天天气晴，最高温度 28 度。",
        {EvidenceType.EXTERNAL_TOOL},
    )


def test_single_weak_marker_does_not_establish_candidate_correlation():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-train",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"route": "北京到上海", "train": "G1", "price": 661},
    )

    assert not ledger.has_candidate_overlap(
        "当前上海天气晴，最高温度 28 度。",
        {EvidenceType.EXTERNAL_TOOL},
    )


def test_two_distinct_markers_establish_candidate_correlation():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-train",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"route": "北京到上海", "price": 661},
    )

    assert ledger.has_candidate_overlap(
        "北京到上海的二等座票价为 661 元。",
        {EvidenceType.EXTERNAL_TOOL},
    )


def test_strong_identifier_establishes_candidate_correlation():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-train",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"train": "G1505"},
    )

    assert ledger.has_candidate_overlap(
        "推荐乘坐 G1505 次列车。",
        {EvidenceType.EXTERNAL_TOOL},
    )


def test_successful_empty_receipt_supports_no_result_answer():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")
    ledger.record_success(
        call_id="call-empty",
        producer="railway:get-tickets",
        evidence_types={EvidenceType.EXTERNAL_TOOL},
        result={"success": True, "content": ""},
        policy="allow_empty_success",
    )

    assert ledger.has_candidate_overlap(
        "本次查询暂无符合条件的车票。",
        {EvidenceType.EXTERNAL_TOOL},
        allow_empty=True,
    )


def test_allow_empty_success_records_successful_empty_result():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")

    receipt = ledger.record_success(
        call_id="call-empty-query",
        producer="execute_sql_query",
        evidence_types={EvidenceType.INTERNAL_DATA},
        result={"success": True, "items": []},
        policy="allow_empty_success",
    )

    assert receipt is not None
    assert ledger.has_valid_evidence({EvidenceType.INTERNAL_DATA})


@pytest.mark.parametrize(
    "result",
    [
        "错误：SQL 执行失败",
        "permission denied",
        {"success": False, "items": []},
        {"status": "error", "items": []},
        {"code": 500, "items": []},
        {"message": "SQL 执行失败", "items": []},
        {"message": "数据库连接失败", "items": []},
    ],
)
def test_allow_empty_success_never_records_error_result(result):
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")

    receipt = ledger.record_success(
        call_id="call-error",
        producer="query-tool",
        evidence_types={EvidenceType.INTERNAL_DATA},
        result=result,
        policy="allow_empty_success",
    )

    assert receipt is None
    assert ledger.receipts == ()


def test_successful_knowledge_content_with_failure_topic_is_not_treated_as_tool_error():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")

    receipt = ledger.record_success(
        call_id="call-kb",
        producer="search_knowledge_base",
        evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
        result={
            "success": True,
            "data": {"content": "数据库连接失败排查手册"},
        },
        policy="allow_empty_success",
    )

    assert receipt is not None

    raw_receipt = ledger.record_success(
        call_id="call-kb-raw",
        producer="search_knowledge_base",
        evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
        result="数据库连接失败排查手册",
        policy="allow_empty_success",
    )

    assert raw_receipt is not None


def test_ledger_exposes_all_recorded_evidence_types():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="knowledge-1",
        producer="search_knowledge_base",
        evidence_types={EvidenceType.INTERNAL_KNOWLEDGE},
        result={"items": [{"content": "审批制度"}]},
    )
    ledger.record_success(
        call_id="file-1",
        producer="read_file",
        evidence_types={EvidenceType.USER_FILE},
        result="报告正文",
    )

    assert ledger.available_evidence_types == frozenset(
        {EvidenceType.INTERNAL_KNOWLEDGE, EvidenceType.USER_FILE}
    )
