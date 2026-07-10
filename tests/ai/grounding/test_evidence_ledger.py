import pytest

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType


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


def test_empty_or_error_like_results_do_not_create_receipts():
    ledger = EvidenceLedger(user_id="7", conversation_id="conv-1")

    for result in (
        None,
        "",
        [],
        {},
        "错误：工具调用失败",
        "[TOOL_ERROR] permission denied",
        "执行成功，但查询结果为空",
        "[SUCCESS] 未找到匹配的 Jira 工单",
        "未找到匹配内容，请调整关键词",
        "未找到匹配的会话摘要",
        "子智能体已执行完成，但未产生可交付正文",
        '{"content": "", "citations": []}',
        {"content": "", "rows": []},
        {"success": False, "message": "business failure"},
        {"code": 500, "message": "server error"},
        {"status": "error", "content": "failed"},
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
