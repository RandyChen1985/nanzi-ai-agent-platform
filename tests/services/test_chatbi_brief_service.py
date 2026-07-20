import pytest

from app.services.chatbi_brief_service import (
    BriefInputError,
    UnsupportedBriefClaim,
    build_business_brief,
    compose_business_brief_markdown,
    sanitize_assistant_report,
)


pytestmark = pytest.mark.no_infrastructure


RESULT = {
    "result_id": "result_1",
    "question": "查询本月区域销售额",
    "dataset_name": "sales_ds",
    "rows": {"rows": [{"区域": "华东", "销售额": 120}, {"区域": "华南", "销售额": 80}]},
    "result_summary": "本月区域销售额查询完成",
    "analysis_context": {"time_range": "本月", "metrics": ["销售额"], "dimensions": ["区域"]},
}

ASSISTANT = """### 🎯 核心结论
---
上周 Token 合计约 **943** 万。

### 📊 数据概览
| 日期 | Token |
| --- | --- |
| 07-14 | 2854686 |

```chart
{"type":"line"}
```
"""


def test_business_brief_contains_deterministic_facts_and_evidence_refs():
    brief = build_business_brief(RESULT)
    assert brief["title"] == "本月区域销售额业务简报"
    assert brief["facts"]["row_count"] == 2
    assert brief["facts"]["numeric_summary"]["销售额"]["sum"] == 200.0
    assert brief["evidence"][0]["result_id"] == "result_1"
    assert "数据说明与证据附录" in brief["markdown"]


def test_business_brief_merges_assistant_report_and_appendix():
    brief = build_business_brief(RESULT, assistant_report=ASSISTANT)
    assert brief["facts"]["has_assistant_report"] is True
    assert "核心结论" in brief["markdown"]
    assert "```chart" not in brief["markdown"]
    assert "数据说明与证据附录" in brief["markdown"]
    assert "销售额" in brief["markdown"]


def test_sanitize_assistant_strips_chart_blocks():
    cleaned = sanitize_assistant_report(ASSISTANT)
    assert "```chart" not in cleaned
    assert "在线会话" in cleaned


def test_business_brief_rejects_empty_inputs():
    with pytest.raises(BriefInputError):
        build_business_brief({**RESULT, "rows": []}, assistant_report="")


def test_business_brief_allows_assistant_only_when_rows_empty():
    brief = build_business_brief({**RESULT, "rows": []}, assistant_report=ASSISTANT)
    assert "核心结论" in brief["markdown"]


def test_business_brief_rejects_claim_without_evidence():
    with pytest.raises(UnsupportedBriefClaim):
        build_business_brief(RESULT, requested_claims=["销售额同比增长 30%"])


def test_compose_markdown_without_assistant_matches_legacy_sections():
    md = compose_business_brief_markdown(RESULT)
    assert "核心数据" in md
    assert "数据说明与证据附录" in md
