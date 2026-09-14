"""数据资产质量治理分计算服务。

纯函数实现：仅消费巡检产出的汇总计数，不访问数据库、不扫描数据内容，
便于单元测试，并可在巡检结算处直接复用。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# 维度权重（合计 100）。口径公开，前端据此展示分数构成。
DIMENSION_WEIGHTS: Dict[str, int] = {
    "consistency": 30,       # 类型一致性：元数据声明类型与物理库一致的字段占比
    "coverage": 25,          # 结构覆盖：元数据字段与物理字段无缺失、无多出
    "documentation": 25,     # 备注完备：字段有有效中文业务备注的占比
    "table_integrity": 20,   # 表完整性：元数据表在物理库中存在的占比
}

# 分数等级：(最低分, 等级键, 中文标签)，按分数从高到低匹配。
LEVEL_THRESHOLDS: List[Tuple[int, str, str]] = [
    (90, "excellent", "优秀"),
    (75, "good", "良好"),
    (60, "fair", "一般"),
    (0, "poor", "较差"),
]

DIMENSION_META: Dict[str, Dict[str, str]] = {
    "consistency": {"label": "类型一致性", "detail": "元数据字段类型与物理库不一致"},
    "coverage": {"label": "结构覆盖", "detail": "元数据与物理库存在字段缺失或多出"},
    "documentation": {"label": "备注完备度", "detail": "字段缺少有效中文业务备注"},
    "table_integrity": {"label": "表完整性", "detail": "元数据表在物理库中已不存在"},
}


def _rate(problem_count: int, total: int) -> float:
    """计算问题占比并 clamp 到 [0, 1]；分母 <= 0 时按无问题处理。"""
    if total <= 0:
        return 0.0
    return min(max(problem_count / total, 0.0), 1.0)


def _level_for(score: int) -> Tuple[str, str]:
    """返回 (等级键, 中文标签)。"""
    for threshold, key, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return key, label
    return "poor", "较差"


def compute_quality_score(
    *,
    tables_scanned: int,
    columns_scanned: int,
    missing_tables_count: int = 0,
    stale_count: int = 0,
    new_count: int = 0,
    mismatch_count: int = 0,
    missing_comment_count: int = 0,
) -> Dict[str, Any]:
    """基于巡检验出的问题计数，计算数据集级质量治理分（0-100）。

    返回 {"score", "level", "level_label", "dimensions"}；
    dimensions 每项含 key/label/weight/score/problem_count/total/detail，供前端解释扣分原因。
    """
    col_total = max(columns_scanned, 0)
    tbl_total = max(tables_scanned, 0)

    problem_counts: Dict[str, int] = {
        "consistency": max(mismatch_count, 0),
        "coverage": max(stale_count, 0) + max(new_count, 0),
        "documentation": max(missing_comment_count, 0),
        "table_integrity": max(missing_tables_count, 0),
    }
    totals: Dict[str, int] = {
        "consistency": col_total,
        "coverage": col_total,
        "documentation": col_total,
        "table_integrity": tbl_total,
    }

    dimensions: List[Dict[str, Any]] = []
    weighted_sum = 0.0
    weight_total = 0
    for key, weight in DIMENSION_WEIGHTS.items():
        count = problem_counts[key]
        rate = _rate(count, totals[key])
        dim_score = int(round((1.0 - rate) * 100))
        dim_score = min(max(dim_score, 0), 100)
        weighted_sum += dim_score * weight
        weight_total += weight
        dimensions.append(
            {
                "key": key,
                "label": DIMENSION_META[key]["label"],
                "weight": weight,
                "score": dim_score,
                "problem_count": count,
                "total": totals[key],
                "detail": DIMENSION_META[key]["detail"],
            }
        )

    overall = int(round(weighted_sum / weight_total)) if weight_total else 100
    overall = min(max(overall, 0), 100)
    level_key, level_label = _level_for(overall)

    return {
        "score": overall,
        "level": level_key,
        "level_label": level_label,
        "dimensions": dimensions,
    }
