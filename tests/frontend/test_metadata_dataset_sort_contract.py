"""契约：元数据数据集列表默认优先显示已启用数据集。"""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "frontend/src/views/MetadataDatasets.vue").read_text(encoding="utf-8")


def test_dataset_sort_prioritizes_enabled_items_before_secondary_sort():
    sort_logic = SOURCE[SOURCE.index("const compareDatasets"):SOURCE.index("const displayDatasets")]

    assert "const statusPriority = Number(b.status === 1) - Number(a.status === 1)" in sort_logic
    assert "if (sortField.value !== 'status')" in sort_logic
    assert "if (statusPriority !== 0) return statusPriority" in sort_logic
    assert "return [...list].sort(compareDatasets)" in SOURCE


def test_quality_score_sort_and_badge_contract():
    """契约：质量分可排序，卡片与列表两处均展示，并带评分时间与降级提示。"""
    sort_logic = SOURCE[SOURCE.index("const compareDatasets"):SOURCE.index("const displayDatasets")]

    # 排序分支与未评分兜底
    assert "case 'quality_score':" in sort_logic
    assert "a.quality_score ?? -1" in sort_logic
    # 排序字段类型包含 quality_score
    assert "type DatasetSortField" in SOURCE and "| 'quality_score'" in SOURCE
    # 卡片与列表两处徽标 + 提示
    assert SOURCE.count("qualityBadgeClass(ds.quality_score)") >= 2
    assert SOURCE.count("qualityTooltip(ds)") >= 2
    # 降级提示与评分时间（后端已写入，前端必须可见）
    assert "degraded" in SOURCE
    assert "quality_scored_at" in SOURCE
