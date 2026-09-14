"""数据集质量治理分纯函数单元测试（不依赖数据库）。"""

from app.services.metadata_quality_score_service import (
    DIMENSION_WEIGHTS,
    compute_quality_score,
)


def test_weights_sum_to_100():
    assert sum(DIMENSION_WEIGHTS.values()) == 100


def test_perfect_dataset_scores_100():
    result = compute_quality_score(
        tables_scanned=3,
        columns_scanned=20,
        missing_tables_count=0,
        stale_count=0,
        new_count=0,
        mismatch_count=0,
        missing_comment_count=0,
    )
    assert result["score"] == 100
    assert result["level"] == "excellent"
    assert all(d["score"] == 100 for d in result["dimensions"])
    assert {d["key"] for d in result["dimensions"]} == set(DIMENSION_WEIGHTS)


def test_empty_dataset_is_full_score():
    """空数据集（0 表 0 列且无问题）不应被扣分。"""
    result = compute_quality_score(tables_scanned=0, columns_scanned=0)
    assert result["score"] == 100
    assert result["level"] == "excellent"


def test_mixed_problems_weighted_score():
    # consistency 80*30 + coverage 80*25 + documentation 70*25 + table_integrity 100*20
    # = 2400 + 2000 + 1750 + 2000 = 8150 -> 82 (good)
    result = compute_quality_score(
        tables_scanned=2,
        columns_scanned=10,
        missing_tables_count=0,
        stale_count=1,
        new_count=1,
        mismatch_count=2,
        missing_comment_count=3,
    )
    assert result["score"] == 82
    assert result["level"] == "good"
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["consistency"]["score"] == 80
    assert by_key["coverage"]["score"] == 80
    assert by_key["documentation"]["score"] == 70
    assert by_key["table_integrity"]["score"] == 100


def test_negative_and_overflow_counts_are_clamped():
    """计数负数按 0 处理；问题数超过分母时该维度封底 0 分，总分不为负。"""
    result = compute_quality_score(
        tables_scanned=1,
        columns_scanned=2,
        missing_tables_count=5,
        stale_count=-3,
        new_count=99,
        mismatch_count=99,
        missing_comment_count=99,
    )
    assert 0 <= result["score"] <= 100
    assert result["level"] == "poor"
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["consistency"]["score"] == 0
    assert by_key["coverage"]["score"] == 0
    assert by_key["documentation"]["score"] == 0
    assert by_key["table_integrity"]["score"] == 0


def test_level_thresholds():
    def level_for(score: int) -> str:
        # 通过构造仅 documentation 维度的问题数逼近目标分。
        # 4 个维度同权重难以精确命中，这里直接断言边界函数行为。
        from app.services.metadata_quality_score_service import _level_for

        return _level_for(score)[0]

    assert level_for(100) == "excellent"
    assert level_for(90) == "excellent"
    assert level_for(89) == "good"
    assert level_for(75) == "good"
    assert level_for(74) == "fair"
    assert level_for(60) == "fair"
    assert level_for(59) == "poor"
    assert level_for(0) == "poor"


def test_breakdown_contains_explainable_fields():
    result = compute_quality_score(tables_scanned=1, columns_scanned=4, mismatch_count=1)
    dim = {d["key"]: d for d in result["dimensions"]}["consistency"]
    assert dim["label"] == "类型一致性"
    assert dim["weight"] == 30
    assert dim["problem_count"] == 1
    assert dim["total"] == 4
    assert isinstance(dim["detail"], str) and dim["detail"]
    assert result["level_label"]
