"""契约测试：数据集「复制新建」向导。

需求（用户原话）：「我某数据集上...的操作里面，加一个复制新建的按钮。然后可以弹框进行向导性新建流程，
支持修改元数据 id 和名称，id 和名称要实时校验有没有重复。然后其他的表，关系等都要复制，可以在向导页面
一步步的看，里面可以编辑或删除，最后一页才确认保存。保存时候要记录初始化变更日志等。」

原型：``prototypes/2026-11-06-metadata-dataset-clone.html``。

这里锁住容易回归的几件事：
- 入口在两个菜单里都有（卡片视图 + 列表视图），且受 ``element:metadata:edit`` 约束；
- 数据源只能下拉选，不允许自由输入；
- 中间步骤不落库，只有第 6 步调一次 ``cloneDataset``；
- 关系在表被删后要能被识别并跳过；
- 初始化日志粒度可选，保存成功后能直达变更日志页签。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VIEW = "frontend/src/views/MetadataDatasets.vue"
WIZARD = "frontend/src/components/metadata/CopyDatasetWizard.vue"
API = "frontend/src/api/metadata.ts"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _wizard() -> str:
    return _read(WIZARD)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def test_entry_button_exists_in_both_card_and_row_menus():
    source = _read(VIEW)

    # 两个菜单都要有，且都走同一个打开函数
    assert source.count('@click="closeCardMenus(); openCopyWizard(ds)"') == 1
    assert source.count("@click=\"withRowMenu((d) => openCopyWizard(d))\"") == 1
    assert source.count("复制新建") >= 4  # 两个按钮文案 + 注释/组件注释


def test_entry_is_gated_by_metadata_edit_permission():
    source = _read(VIEW)
    for marker in [
        'v-if="hasPermission(\'element:metadata:edit\')"\n                type="button"\n                @click="closeCardMenus(); openCopyWizard(ds)"',
    ]:
        assert marker in source

    row_block = source[
        source.index("@click=\"withRowMenu((d) => openCopyWizard(d))\"") - 260 :
        source.index("@click=\"withRowMenu((d) => openCopyWizard(d))\"")
    ]
    assert 'v-if="hasPermission(\'element:metadata:edit\')"' in row_block


def test_wizard_is_mounted_and_refreshes_list_after_creation():
    source = _read(VIEW)
    assert "<CopyDatasetWizard" in source
    assert ':show="showCopyWizard"' in source
    assert ':dataset="copySourceDataset"' in source
    assert '@created="handleDatasetCopied"' in source
    assert "void fetchDatasets()" in source


# ---------------------------------------------------------------------------
# 第 1 步：实时查重 + 数据源下拉
# ---------------------------------------------------------------------------


def test_realtime_uniqueness_check_with_debounce():
    source = _wizard()
    assert "metadataApi.checkDatasetUnique" in source
    # 350ms 防抖：每敲一个字就发请求会把接口打爆
    assert "setTimeout(() => void runCheck(), 350)" in source
    # 竞态保护：慢请求不能覆盖新输入的结果
    assert "seq !== checkSeq" in source
    assert "'checking'" in source and "'taken'" in source


def test_data_source_is_dropdown_only():
    source = _wizard()
    assert "metadataApi.listDbConnectionConfigs" in source
    assert "pickDataSource" in source
    # 不允许自由输入：data_source 只能通过下拉点击赋值
    assert 'v-model="draft.data_source"' not in source
    # 没有权限时退化为「仅沿用当前数据源」，而不是报错卡住
    assert "dataSourceDenied" in source


def test_changing_data_source_shows_risk_hint():
    source = _wizard()
    assert "dataSourceChanged" in source
    assert "Schema 巡检" in source


# ---------------------------------------------------------------------------
# 第 2-4 步：可编辑可删除
# ---------------------------------------------------------------------------


def test_tables_columns_metrics_relationships_are_editable_and_removable():
    source = _wizard()
    assert "function removeTable" in source
    assert "function restoreTables" in source
    assert "removedTables" in source
    assert "function startEditTable" in source
    assert 'drawer.type === \'column\'' in source
    assert "function removeRelationship" in source
    assert "function removeMetric" in source
    assert "function saveMetric" in source


def test_columns_can_be_removed_and_restored():
    """字段和整张表一样是可删的：删错了要能按原位置恢复。"""
    source = _wizard()
    assert "function removeColumn" in source
    assert "removedColumns" in source
    assert "function restoreColumns" in source
    # 恢复要按原下标插回，否则同表删多列后顺序会错
    assert "sort((a, b) => a.index - b.index)" in source
    assert "恢复已移除的 {{ removedColumns.length }} 个字段" in source
    # 字段行与抽屉里都要有删除入口
    assert "removeColumn(row.index, columnIndex)" in source
    assert "删除此字段" in source


def test_can_add_tables_and_columns():
    """向导不只「删」，也要能加：空草稿里也能建出一张表、给表加字段。"""
    source = _wizard()
    assert "function openNewTable" in source
    assert "function saveTable" in source
    assert "新增表" in source
    # 新增表要能填物理表名，并挡住重名
    assert "表的物理名不能为空" in source
    assert "已存在于副本中" in source
    # 字段：新增走同一个抽屉（columnIndex = -1），保存时校验物理名与重名
    assert "function saveColumn" in source
    assert "openColumn(row.index, null)" in source
    assert "字段物理名不能为空" in source
    assert "已存在于 " in source
    # 新增表后自动展开，方便接着加字段
    assert "expanded.value[physicalName] = true" in source


def test_relationships_pointing_to_removed_tables_are_flagged_and_skipped():
    source = _wizard()
    assert "validRelationships" in source
    assert "brokenRelationshipCount" in source
    assert "清理失效关系" in source
    # 提交时只送有效关系，避免后端逐条跳过还占日志
    assert "relationships: validRelationships.value.map" in source


# ---------------------------------------------------------------------------
# 第 5-6 步：选项、日志、最后一步才落库
# ---------------------------------------------------------------------------


def test_clone_api_is_called_only_on_final_submit():
    source = _wizard()
    # 整个组件里只有 submit 一处调用
    assert source.count("metadataApi.cloneDataset") == 1
    submit_index = source.index("async function submit()")
    assert "metadataApi.cloneDataset" in source[submit_index:]


def test_log_granularity_and_preview():
    source = _wizard()
    assert "logMode" in source
    assert "仅 1 条汇总" in source
    assert "writtenLogCount" in source
    assert "log_mode: opts.logMode" in source


def test_default_options_do_not_copy_permissions():
    source = _wizard()
    options_block = source[source.index("const opts = reactive({") : source.index("const unique = reactive")]
    assert "copyPerm: false" in options_block
    assert "logMode: 'detail'" in options_block


def test_success_page_links_to_changelog_tab():
    source = _wizard()
    assert "查看初始化变更日志" in source
    assert "query: { tab: 'changelog' }" in source


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------


def test_api_exposes_check_unique_and_clone_endpoints():
    source = _read(API)
    assert "checkDatasetUnique" in source
    assert "`${API_BASE}/datasets/check-unique`" in source
    assert "cloneDataset" in source
    assert "`${API_BASE}/datasets/${sourceId}/clone`" in source
    # 提交的是草稿内容，必须带全量 tables / relationships / metrics
    for field in ["tables: CloneTableInput[]", "relationships: CloneRelationshipInput[]", "metrics: CloneMetricInput[]"]:
        assert field in source
