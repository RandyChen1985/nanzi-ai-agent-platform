"""契约测试：列表视图批量删除数据集。

需求（用户原话）：「list 视图，加个复选框，我需要支持批量删除元数据集」。

锁住四件事：
- 复选框只对能删的人出现（没有 ``element:metadata:delete`` 时列表保持原样）；
- 表头全选 / 半选状态可用，行内点击复选框不触发「进入数据集」；
- 确认弹窗列出将删内容，走 `batchDeleteDatasets`，成功后清空选择并刷新；
- 后端接口对空入参 / 超量 / 不存在 id 的处理。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VIEW = "frontend/src/views/MetadataDatasets.vue"
API = "frontend/src/api/metadata.ts"
ENDPOINT = "app/api/portal/endpoints/metadata.py"
SCHEMAS = "app/schemas/metadata.py"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_checkboxes_are_gated_by_delete_permission():
    source = _read(VIEW)
    assert "const canBatchDelete = computed(() => hasPermission('element:metadata:delete'))" in source
    # 表头与行内复选框都要受这个开关控制
    assert source.count('v-if="canBatchDelete"') >= 2


def test_row_checkbox_does_not_navigate():
    source = _read(VIEW)
    row_input = source[source.index('v-if="canBatchDelete"') :]
    assert "@click.stop" in row_input
    assert "@change=\"toggleSelect(ds.id)\"" in source


def test_select_all_supports_partial_state():
    source = _read(VIEW)
    assert 'ref="selectAllRef"' in source
    assert "indeterminate = someVisibleSelected.value && !allVisibleSelected.value" in source
    # 半选是 DOM property，必须显式同步，不能靠 :indeterminate 绑定
    assert 'indeterminate="' not in source


def test_selection_clears_when_filters_change():
    source = _read(VIEW)
    block = source[source.index("watch([searchQuery, statusFilter, sortField, sortDirection]") :]
    assert "clearSelection()" in block[:400]


def test_batch_delete_uses_confirm_modal_and_refreshes():
    source = _read(VIEW)
    assert "showBatchDeleteModal" in source
    assert "metadataApi.batchDeleteDatasets([...selectedDatasetIds.value])" in source
    assert "批量删除" in source
    # 删除成功要清空选择并刷新列表，否则会留下指向已删项的勾选
    tail = source[source.index("const confirmBatchDelete"):]
    assert "clearSelection()" in tail[:1600]
    assert "await fetchDatasets()" in tail[:1600]
    # 提示分三档：成功 / 失败 / 已不存在
    for word in ["已删除", "删除失败", "已不存在"]:
        assert word in tail[:1600]


def test_api_exposes_batch_delete():
    source = _read(API)
    assert "batchDeleteDatasets" in source
    assert "`${API_BASE}/datasets/batch-delete`" in source


def test_backend_endpoint_validates_and_reports():
    source = _read(ENDPOINT)
    block = source[source.index('"/datasets/batch-delete"') :]
    block = block[: block.index("@router.get")]
    assert "element:metadata:delete" in block
    assert "请先选择要删除的数据集" in block
    assert "单次最多删除 100 个数据集" in block
    assert "missing" in block and "failed" in block
    # 逐条复用单条删除逻辑：RAGFlow / 向量 / 日志的清理不需要重写一遍
    assert "MetadataService.delete_dataset" in block

    schemas = _read(SCHEMAS)
    assert "class BatchDeleteDatasetsRequest" in schemas
