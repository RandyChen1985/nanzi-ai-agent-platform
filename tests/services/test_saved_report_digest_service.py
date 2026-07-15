from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.no_infrastructure


def _report(title="机器负载日报"):
    return SimpleNamespace(title=title, description="每日机器资源使用情况")


def _run(snapshot, row_count=None):
    return SimpleNamespace(
        id=31,
        result_snapshot=snapshot,
        row_count=row_count,
        finished_at=None,
        resolved_params={"日期": "2026-07-15"},
    )


def test_mobile_digest_limits_records_and_fields_and_formats_numbers():
    from app.services.saved_report_digest_service import build_deterministic_digest

    rows = [
        {"服务器": f"server-{i:02d}", "CPU": 0.963, "内存": 82.4, "请求数": 126800, "状态": "异常", "机房": "上海"}
        for i in range(8)
    ]
    digest = build_deterministic_digest(_report(), _run({"rows": rows}, row_count=8), {"日期": "2026-07-15"})

    assert len(digest["records"]) == 5
    assert all(len(record) <= 5 for record in digest["records"])
    assert digest["records"][0]["请求数"] == "12.68万"
    assert digest["generation_mode"] == "fallback"


def test_mobile_digest_supports_columns_and_array_rows_and_empty_results():
    from app.services.saved_report_digest_service import build_deterministic_digest

    populated = build_deterministic_digest(
        _report(),
        _run({"columns": ["部门", "工单数"], "rows": [["运维", 32], ["研发", 18]]}),
        {},
    )
    empty = build_deterministic_digest(_report(), _run({"columns": ["部门"], "rows": []}, row_count=0), {})

    assert populated["records"][0] == {"部门": "运维", "工单数": "32"}
    assert "暂无符合条件的数据" in empty["key_findings"]


def test_digest_scope_uses_chinese_readable_range_not_raw_param_keys():
    from app.services.saved_report_digest_service import build_deterministic_digest, render_mobile_markdown

    digest = build_deterministic_digest(
        _report(),
        _run({"rows": [{"日期": "2026-07-06", "用量": 2}]}),
        {
            "date_range": "month_start_to_today",
            "start_datetime": "2026-07-01 00:00:00",
            "end_datetime": "2026-07-15 23:59:59",
        },
    )
    _, content = render_mobile_markdown(digest, "https://example.test/report/1", "inbox")

    assert "date_range" not in digest["scope"]
    assert "start_datetime" not in digest["scope"]
    assert "end_datetime" not in digest["scope"]
    assert "month_start_to_today" not in digest["scope"]
    assert "本月至今" in digest["scope"]
    assert "2026-07-01" in digest["scope"]
    assert "2026-07-15" in digest["scope"]
    assert "统计范围：本月至今" in content or "统计范围：" in content and "本月至今" in content


def test_digest_humanizes_object_column_keys_instead_of_dict_repr():
    from app.services.saved_report_digest_service import build_deterministic_digest, render_mobile_markdown

    digest = build_deterministic_digest(
        _report(),
        _run(
            {
                "columns": [
                    {"name": "stat_date", "type": "3", "label": "统计日期"},
                    {"name": "new_users", "type": "8"},
                ],
                "rows": [
                    ["2026-07-06", 2],
                    ["2026-07-10", 16],
                ],
            }
        ),
        {},
    )
    _, content = render_mobile_markdown(digest, "https://example.test/report/1", "inbox")

    assert digest["records"][0] == {"统计日期": "2026-07-06", "new_users": "2"}
    assert "{'name'" not in content
    assert '"type"' not in content
    assert "统计日期：2026-07-06" in content or "**1. 2026-07-06**" in content
    assert "new_users：2" in content


def test_deterministic_digest_surfaces_actual_kpi_values_without_ai():
    from app.services.saved_report_digest_service import build_deterministic_digest

    digest = build_deterministic_digest(
        _report(),
        _run({"rows": [{"平均CPU": "68.3%", "异常机器": 3, "最高负载": "96%"}]}),
        {},
    )

    assert "平均CPU：68.3%" in digest["key_findings"]
    assert "异常机器：3" in digest["key_findings"]


@pytest.mark.parametrize("channel", ["dingtalk", "wechat_work"])
def test_mobile_markdown_uses_vertical_records_without_wide_tables(channel):
    from app.services.saved_report_digest_service import build_deterministic_digest, render_mobile_markdown

    digest = build_deterministic_digest(
        _report(),
        _run({"rows": [{"服务器": "server-03", "CPU": "96%", "状态": "异常"}]}),
        {},
    )
    title, content = render_mobile_markdown(digest, "https://example.test/report/31", channel)

    assert title == "机器负载日报"
    assert "### 核心结论" in content
    assert "**1. server-03**" in content
    assert "CPU：96%" in content
    assert "[查看完整报表]" in content
    assert "|---" not in content


@pytest.mark.asyncio
async def test_ai_enrichment_accepts_grounded_structured_findings():
    from app.services.saved_report_digest_service import enrich_digest_with_ai

    digest = {
        "title": "机器负载日报", "scope": "日期：2026-07-15",
        "key_findings": ["本次共查询到 2 条数据"],
        "records": [{"服务器": "server-03", "CPU": "96%"}],
        "analysis": [], "risk_note": None, "generation_mode": "fallback", "ai_status": "disabled",
    }

    async def generator(_prompt):
        return '{"key_findings":["server-03 的 CPU 为 96%"],"analysis":["该服务器在当前结果中数值最高"],"risk_note":"建议优先核查 server-03"}'

    result = await enrich_digest_with_ai(digest, enabled=True, generator=generator)

    assert result["generation_mode"] == "ai"
    assert result["ai_status"] == "success"
    assert result["key_findings"] == ["server-03 的 CPU 为 96%"]


@pytest.mark.asyncio
async def test_ai_enrichment_failure_keeps_deterministic_digest():
    from app.services.saved_report_digest_service import enrich_digest_with_ai

    digest = {
        "title": "机器负载日报", "scope": "按订阅条件",
        "key_findings": ["本次共查询到 1 条数据"], "records": [],
        "analysis": [], "risk_note": None, "generation_mode": "fallback", "ai_status": "disabled",
    }

    async def generator(_prompt):
        raise TimeoutError("model timeout")

    result = await enrich_digest_with_ai(digest, enabled=True, generator=generator)

    assert result["key_findings"] == ["本次共查询到 1 条数据"]
    assert result["generation_mode"] == "fallback"
    assert result["ai_status"] == "fallback"


@pytest.mark.asyncio
async def test_ai_enrichment_includes_optional_subscription_instruction():
    from app.services.saved_report_digest_service import enrich_digest_with_ai

    captured = {}
    digest = {
        "title": "机器负载日报", "scope": "按订阅条件",
        "key_findings": ["本次共查询到 1 条数据"], "records": [{"服务器": "server-03"}],
        "analysis": [], "risk_note": None, "generation_mode": "fallback", "ai_status": "disabled",
    }

    async def generator(prompt):
        captured["prompt"] = prompt
        return '{"key_findings":["server-03 需要关注"],"analysis":["当前结果仅包含一台机器"],"risk_note":null}'

    result = await enrich_digest_with_ai(
        digest,
        enabled=True,
        analysis_instruction="只展示异常机器，并使用管理层语言",
        generator=generator,
    )

    assert "只展示异常机器，并使用管理层语言" in captured["prompt"]
    assert result["analysis_instruction"] == "只展示异常机器，并使用管理层语言"
