"""实体关系 / 业务指标 tab 徽标数字的实时联动契约。

详情页两个徽标读的是 dataset.relationship_count / dataset.metric_count
（首次加载时后端算的值），而关系、指标增删只刷新了子组件自己的列表，
父级 dataset 不会重新拉取，于是出现「列表 2 条、徽标还是 1」的错位。
契约锁住：列表刷新后把数量抛给父级，父级覆盖 dataset 上的计数字段。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.no_infrastructure

RELATIONSHIP_LIST = ROOT / "frontend/src/components/metadata/RelationshipList.vue"
METRIC_LIST = ROOT / "frontend/src/components/metadata/MetricList.vue"
TABLES_VIEW = ROOT / "frontend/src/views/MetadataTables.vue"


def test_relationship_list_emits_latest_count_after_fetch():
    source = RELATIONSHIP_LIST.read_text(encoding="utf-8")

    assert '"count-change"' in source or "'count-change'" in source
    # 必须在刷新列表之后发，且用列表长度（与后端 relationship_count 同口径）
    assert 'emit("count-change", relationships.value.length)' in source
    fetch_block = source[source.index("const fetchRelationships = async () => {"):]
    assert "emit(\"count-change\"" in fetch_block.split("const fetchAllTables")[0]


def test_metric_list_emits_latest_count_after_fetch():
    source = METRIC_LIST.read_text(encoding="utf-8")

    assert "'count-change'" in source or '"count-change"' in source
    assert "emit('count-change', metrics.value.length)" in source
    fetch_block = source[source.index("const fetchMetrics = async () => {"):]
    assert "emit('count-change'" in fetch_block.split("const openCreate")[0]


def test_dataset_detail_listens_and_updates_both_badge_counts():
    source = TABLES_VIEW.read_text(encoding="utf-8")

    # 关系
    assert '@count-change="handleRelationshipCountChange"' in source
    assert "const handleRelationshipCountChange = (count: number) => {" in source
    assert "dataset.value.relationship_count = count" in source
    # 指标
    assert '@count-change="handleMetricCountChange"' in source
    assert "const handleMetricCountChange = (count: number) => {" in source
    assert "dataset.value.metric_count = count" in source
    # 徽标仍以 dataset 为准，不能改成只读列表的局部状态
    assert "dataset?.relationship_count || 0" in source
    assert "dataset?.metric_count || 0" in source
