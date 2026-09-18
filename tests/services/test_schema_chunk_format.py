"""schema_chunk_format 单元测试。"""
from app.services.schema_chunk_format import (
    count_schema_hits,
    detect_schema_ambiguity,
    estimate_text_tokens,
    extract_schema_ambiguity_candidates,
    extract_schema_confidence_values,
    format_schema_chunk,
    format_schema_hit_summary,
    format_schema_hits,
    infer_schema_chunk_meta,
)


def test_infer_schema_chunk_meta_table():
    content = "table_name: dim_region\ndataset: sales_detail\n"
    chunk_type, table, dataset = infer_schema_chunk_meta(content, "sales_detail.dim_region.txt")
    assert chunk_type == "table"
    assert table == "dim_region"
    assert dataset == "sales_detail"


def test_infer_schema_chunk_meta_metrics():
    content = (
        "dataset: sales_detail\n"
        "meta_name: 销售明细\n"
        "metrics_scope: 销售明细\n"
        "metrics: []\n"
    )
    chunk_type, table, dataset = infer_schema_chunk_meta(content, "_metrics.txt")
    assert chunk_type == "metrics"
    assert table is None
    assert dataset == "sales_detail"


def test_infer_schema_chunk_meta_metrics_legacy_without_dataset_field():
    content = "metrics_scope: 销售明细\nmetrics: []\n"
    chunk_type, table, dataset = infer_schema_chunk_meta(content, "_metrics.txt")
    assert chunk_type == "metrics"
    assert table is None
    assert dataset == "销售明细"


def test_format_schema_chunk_table():
    body = "table_name: dim_region\ndataset: sales_detail\n"
    out = format_schema_chunk(1, body, score=0.86, doc_name="sales_detail.dim_region.txt")
    assert out.startswith("--- [Schema:1] type=table dataset=sales_detail table=dim_region score=0.86 ---")
    assert "table_name: dim_region" in out


def test_format_schema_hits_joins_chunks():
    text = format_schema_hits([
        {
            "content": "table_name: t1\ndataset: ds1\n",
            "similarity": 0.9,
            "doc_name": "ds1.t1.txt",
            "metadata_dataset_id": 1,
        },
        {
            "content": "table_name: t2\ndataset: ds1\n",
            "similarity": 0.8,
            "doc_name": "ds1.t2.txt",
            "metadata_dataset_id": 1,
        },
    ])
    assert "--- [Schema:1]" in text
    assert "--- [Schema:2]" in text
    assert "score=0.90" in text
    assert "score=0.80" in text
    assert "dataset_id=1" in text


def test_extract_schema_confidence_values_new_and_legacy():
    new_fmt = "--- [Schema:1] type=table dataset=ds table=t score=0.86 ---\ntable_name: t\n"
    legacy = "[置信度: 0.54]\n--- Source: a.txt ---\ntable_name: a\n"
    assert extract_schema_confidence_values(new_fmt) == [0.86]
    assert extract_schema_confidence_values(legacy) == [0.54]
    assert extract_schema_confidence_values(new_fmt + "\n\n" + legacy) == [0.86, 0.54]


def test_detect_schema_ambiguity_new_format():
    text = (
        "--- [Schema:1] type=table dataset=ds1 table=access_log score=0.88 ---\n"
        "table_name: access_log\n"
        "\n"
        "--- [Schema:2] type=table dataset=ds2 table=audit_log score=0.86 ---\n"
        "table_name: audit_log\n"
    )
    ambiguous, reason = detect_schema_ambiguity(text)
    assert ambiguous is True
    assert "多个高置信度" in reason


def test_same_dataset_multiple_tables_are_not_ambiguous():
    text = (
        "--- [Schema:1] type=table dataset=ds1 table=access_log score=0.88 dataset_id=1 ---\n"
        "table_name: access_log\n"
        "\n"
        "--- [Schema:2] type=table dataset=ds1 table=audit_log score=0.86 dataset_id=1 ---\n"
        "table_name: audit_log\n"
    )
    ambiguous, reason = detect_schema_ambiguity(text)
    assert ambiguous is False
    assert reason == ""


def test_extract_schema_ambiguity_candidates_groups_by_dataset():
    text = (
        "--- [Schema:1] type=table dataset=门禁数据 table=entrance_log score=0.88 dataset_id=1 ---\n"
        "table_name: entrance_log\n"
        "\n"
        "--- [Schema:2] type=table dataset=审计数据 table=audit_log score=0.86 dataset_id=2 ---\n"
        "table_name: audit_log\n"
    )
    candidates = extract_schema_ambiguity_candidates(text)
    assert [item["id"] for item in candidates] == ["1", "2"]
    assert candidates[0]["label"] == "门禁数据"
    assert "entrance_log" in candidates[0]["description"]


def test_format_schema_hit_summary():
    text = (
        "--- [Schema:1] type=table dataset=ds table=t1 score=0.86 ---\n"
        "table_name: t1\n\n"
        "--- [Schema:2] type=table dataset=ds table=t2 score=0.80 ---\n"
        "table_name: t2\n"
    )
    assert count_schema_hits(text) == 2
    token_est = estimate_text_tokens(text)
    assert token_est >= 1
    summary = format_schema_hit_summary(text)
    assert summary == f"[命中摘要] 共命中 2 条元数据记录，占用约 {token_est} token"


def test_format_schema_hit_summary_returns_none_without_hits():
    assert format_schema_hit_summary("No relevant schema info found.") is None


def test_estimate_text_tokens_uses_runtime_utf8_byte_budget():
    """估算口径必须与 AgentScope `count_tokens`（UTF-8 字节数 ÷ 4）一致。

    这是上下文裁剪水位线、占用展示与运行时判定能否对齐的前提。历史上按
    `cjk * 1.5 + other / 4` 加权，对中文高估约 2 倍，会让中文会话在模型窗口只用掉
    约三分之一时就被判定超预算并压缩；对英文反而低估（0.25 < 实际约 0.3）可能超窗。
    """
    for text in ("上" * 100, "a" * 100, "上" * 50 + "a" * 50, "帮我查一下上个月的销售数据"):
        expected = max(1, int(len(text.encode("utf-8")) / 4 + 0.5))
        assert estimate_text_tokens(text) == expected, text


def test_estimate_text_tokens_does_not_overestimate_chinese():
    """中文不得再被高估：100 个汉字应按字节口径算 75，而非加权口径的 150。"""
    assert estimate_text_tokens("上" * 100) == 75


def test_estimate_text_tokens_empty_is_zero():
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens(None) == 0


def test_detect_schema_ambiguity_legacy_format():
    text = (
        "[置信度: 0.88]\n--- Source: access_log.md ---\n数据集: 访问日志\n"
        "\n[置信度: 0.86]\n--- Source: audit_log.md ---\n数据集: 操作审计\n"
    )
    ambiguous, reason = detect_schema_ambiguity(text)
    assert ambiguous is True
    assert "多个高置信度" in reason
