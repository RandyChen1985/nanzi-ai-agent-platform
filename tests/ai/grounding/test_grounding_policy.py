import pytest

from app.services.ai.grounding.ledger import EvidenceLedger
from app.services.ai.grounding.models import EvidenceType
from app.services.ai.grounding.policy import (
    FactRequirement,
    GroundingAction,
    evaluate_grounding,
    resolve_fact_requirement,
    evidence_types_for_capabilities,
)
from app.services.ai.request_decision import RequestCapability, RequestDecision, RequestSource


pytestmark = pytest.mark.no_infrastructure


def _decision(source: RequestSource, capability: RequestCapability) -> RequestDecision:
    return RequestDecision(
        source=source,
        capability=capability,
        confidence=0.9,
        reasoning="test",
    )


def test_internal_data_source_requires_matching_evidence():
    requirement = resolve_fact_requirement(
        _decision(RequestSource.INTERNAL_STRUCTURED_DATA, RequestCapability.DATA_QUERY)
    )

    assert requirement.required
    assert requirement.accepted_types == frozenset({EvidenceType.INTERNAL_DATA})


def test_current_conversation_context_is_already_available_evidence():
    requirement = resolve_fact_requirement(
        _decision(RequestSource.CONVERSATION_CONTEXT, RequestCapability.CONTEXT_TRANSFORM)
    )

    assert not requirement.required


def test_unknown_request_passes_numeric_table_without_evidence():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    answer = (
        "好的，我来调用数据智能体进行分析。\n"
        "| 排名 | 姓名 | 数量 | 金额 |\n"
        "| --- | --- | --- | --- |\n"
        "| 1 | 王强 | 1193 | ¥663.98万 |"
    )

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_dynamic_fact_passes_without_evidence():
    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text="张三是当前表现最好的员工。",
        ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_dynamic_fact_passes_with_any_valid_tool_receipt():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-time",
        producer="clock",
        evidence_types={EvidenceType.RUNTIME_STATE},
        result="12:00",
    )

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text="张三是当前表现最好的员工。",
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_structured_fact_passes_with_any_valid_tool_receipt():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-travel",
        producer="travel-query",
        evidence_types={EvidenceType.PUBLIC_WEB},
        result={"trains": [{"number": "G1505", "price": 973}]},
    )
    answer = (
        "| 车次 | 出发时间 | 票价 |\n"
        "| --- | --- | --- |\n"
        "| G1505 | 07:50 | 973元 |"
    )

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_runtime_fact_passes_when_matching_runtime_receipt_exists():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-bash",
        producer="Bash",
        evidence_types={EvidenceType.RUNTIME_STATE},
        result="Filesystem Size Used Avail Capacity\n/dev/disk3s1s1 460Gi 12Gi 55Gi 18%",
    )
    answer = (
        "根据检查结果，根分区 /dev/disk3s1s1 总容量 460Gi，已用 12Gi，"
        "可用 55Gi，使用率 18%。"
    )

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_internal_table_passes_with_unrelated_runtime_receipt():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-bash",
        producer="Bash",
        evidence_types={EvidenceType.RUNTIME_STATE},
        result="ok",
    )
    answer = (
        "| 排名 | 姓名 | 金额 |\n"
        "| --- | --- | --- |\n"
        "| 1 | 王强 | ¥663.98万 |"
    )

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_mixed_fact_passes_with_any_valid_tool_receipt():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-bash",
        producer="Bash",
        evidence_types={EvidenceType.RUNTIME_STATE},
        result="disk usage 18%",
    )
    answer = "当前磁盘使用率为18%。业务员销售额排名：王强第一，金额 ¥663.98万。"

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


@pytest.mark.parametrize(
    "answer",
    [
        "该附件作者是李四。",
        "这家公司成立于2001年。",
        "该制度要求三级审批。",
    ],
)
def test_unknown_entity_fact_without_evidence_passes(answer):
    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
    )

    assert decision.action == GroundingAction.PASS


def test_unknown_creative_and_math_outputs_are_not_misclassified_as_external_facts():
    requirement = resolve_fact_requirement(
        _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
    )
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")

    poem = evaluate_grounding(
        requirement=requirement,
        candidate_text="写一首短诗：春风穿过树林，月光落在窗前。",
        ledger=ledger,
    )
    math = evaluate_grounding(
        requirement=requirement,
        candidate_text="2 + 2 = 4。",
        ledger=ledger,
    )

    assert poem.action == GroundingAction.PASS
    assert math.action == GroundingAction.PASS


@pytest.mark.parametrize(
    "answer",
    [
        "问题原因是配置错误。",
        "推荐做法为先备份再修改。",
        "布尔值是 True。",
    ],
)
def test_unknown_explanations_are_not_treated_as_external_entity_facts(answer):
    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text=answer,
        ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
    )

    assert decision.action == GroundingAction.PASS


def test_single_ambiguous_claim_accepts_one_matching_alternative_source():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-web",
        producer="web-search",
        evidence_types={EvidenceType.PUBLIC_WEB},
        result="官网文档内容",
    )

    decision = evaluate_grounding(
        requirement=resolve_fact_requirement(
            _decision(RequestSource.UNKNOWN, RequestCapability.ANSWER)
        ),
        candidate_text="公司官网文档显示，该产品目前支持标准 API。",
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_stable_general_knowledge_and_hypothetical_examples_pass_without_evidence():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    general = resolve_fact_requirement(
        _decision(RequestSource.GENERAL, RequestCapability.ANSWER)
    )

    stable = evaluate_grounding(
        requirement=general,
        candidate_text="HTTP 404 表示请求的资源未找到。",
        ledger=ledger,
    )
    hypothetical = evaluate_grounding(
        requirement=general,
        candidate_text="假设示例：某员工本月销售额为 100 万元，仅用于演示计算方法。",
        ledger=ledger,
    )

    assert stable.action == GroundingAction.PASS
    assert hypothetical.action == GroundingAction.PASS


def test_matching_receipt_allows_evidence_required_answer():
    ledger = EvidenceLedger(user_id="1", conversation_id="c1")
    ledger.record_success(
        call_id="call-1",
        producer="data-agent",
        evidence_types={EvidenceType.INTERNAL_DATA},
        result={"rows": [{"name": "王强", "amount": 100}]},
    )
    requirement = resolve_fact_requirement(
        _decision(RequestSource.INTERNAL_STRUCTURED_DATA, RequestCapability.DATA_QUERY)
    )

    decision = evaluate_grounding(
        requirement=requirement,
        candidate_text="王强本月金额为 100 万元。",
        ledger=ledger,
    )

    assert decision.action == GroundingAction.PASS


def test_model_cannot_self_authorize_method_only_answer_when_evidence_is_unavailable():
    requirement = resolve_fact_requirement(
        _decision(RequestSource.RUNTIME_DIAGNOSTIC, RequestCapability.RUNTIME_TOOL)
    )

    decision = evaluate_grounding(
        requirement=requirement,
        candidate_text="我目前没有读取运行状态。可以先检查服务进程和端口，再根据结果分析。",
        ledger=EvidenceLedger(user_id="1", conversation_id="c1"),
    )

    assert decision.action == GroundingAction.BLOCK_UNGROUNDED_FACTS


def test_agent_capabilities_map_to_evidence_types_without_agent_names():
    assert evidence_types_for_capabilities(["data_query", "knowledge_base"]) == frozenset(
        {EvidenceType.INTERNAL_DATA, EvidenceType.INTERNAL_KNOWLEDGE}
    )
    assert evidence_types_for_capabilities(["web_search", "file_read"]) == frozenset(
        {EvidenceType.PUBLIC_WEB, EvidenceType.USER_FILE}
    )


def test_block_decision_exposes_missing_evidence_types_for_ui_actions():
    ledger = EvidenceLedger(user_id="u1", conversation_id="c1")
    requirement = FactRequirement(
        required=True,
        accepted_types=frozenset({EvidenceType.RUNTIME_STATE}),
    )

    decision = evaluate_grounding(
        requirement=requirement,
        candidate_text="当前磁盘使用率为 95%。",
        ledger=ledger,
    )

    assert decision.action == GroundingAction.BLOCK_UNGROUNDED_FACTS
    assert decision.required_evidence_types == frozenset({EvidenceType.RUNTIME_STATE})
