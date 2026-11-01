"""「绑定数据集」弹窗的搜索与筛选契约。

过滤规则抽到 utils/metadataDatasetFilter.ts，因此核心断言是**真跑**：
esbuild 连同依赖一起打包成 CJS 后在 Node 里执行，比对实际过滤结果。
组件层只断言接线与结构。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODAL = ROOT / "frontend/src/components/agent/MetadataDatasetBindingModal.vue"
FILTER_UTIL = ROOT / "frontend/src/utils/metadataDatasetFilter.ts"
PROBE = ROOT / "tests/frontend/metadata_dataset_filter_probe.cjs"


def _modal() -> str:
    return MODAL.read_text(encoding="utf-8")


def _run_probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：过滤规则
# --------------------------------------------------------------------------


def test_keyword_covers_display_name_internal_name_description_source_and_tags():
    """用户给的线索很杂：可能是显示名、内部名、描述片段、数据源名或标签，只搜名称会漏一半。"""
    out = _run_probe()
    assert out["kw_display_name"] == [7, 1]
    assert out["kw_internal_name"] == [7]
    assert out["kw_description"] == [53]
    assert out["kw_data_source"] == [99]
    assert out["kw_tag"] == [1, 99]
    assert out["kw_case_insensitive"] is True


def test_data_source_filter():
    out = _run_probe()
    assert out["ds_mysql"] == [1, 7]
    assert out["ds_none_matches"] is True


def test_sorting():
    out = _run_probe()
    assert out["sort_tables"] == [99, 1, 53, 7]
    assert out["sort_updated"] == [7, 1, 53, 99]


def test_name_sort_keeps_chinese_order():
    """只锁中文之间的相对顺序：拉丁名与汉字的混排位置由 ICU 决定，跨平台会变。"""
    order = _run_probe()["sort_name"]
    assert order.index(99) < order.index(7) < order.index(1)


def test_sort_tie_breaks_on_id():
    assert _run_probe()["sort_tie"] == [2, 9]


def test_updated_sort_falls_back_to_created_at():
    """没有 updated_at 的数据集不能因此被判成「最旧」以外的东西，也不能丢。"""
    assert _run_probe()["sort_updated_fallback_created_at"] is True


def test_data_source_options_are_deduped_and_sorted():
    out = _run_probe()
    assert out["options"] == ["clickhouse", "mysql_aiagent", "postgresql_demo"]
    assert out["counts"] == {"mysql_aiagent": 2, "postgresql_demo": 1, "clickhouse": 1}


def test_has_any_filter():
    out = _run_probe()
    assert out["no_filter"] is True
    assert out["kw_filter"] is True
    assert out["ds_filter"] is True


def test_label_falls_back_to_name_then_id():
    out = _run_probe()
    assert out["label_fallback_name"] is True
    assert out["label_fallback_id"] is True


# --------------------------------------------------------------------------
# 组件接线
# --------------------------------------------------------------------------


def test_modal_wires_the_filter_utility():
    source = _modal()
    assert "filterAndSortMetadataDatasets(" in source
    assert "listDataSourceOptions(" in source
    assert "hasAnyMetadataFilter(" in source


def test_list_renders_filtered_result_and_reports_counts():
    source = _modal()
    assert 'v-for="dataset in filteredDatasets"' in source
    assert "筛选出" in source
    assert "全选当前结果" in source


def test_search_input_follows_project_conventions():
    """搜索框统一 type="search"；固定高度控件必须 py-0，否则全局上下 padding 会切字。"""
    source = _modal()
    import re

    search_tag = next(
        tag
        for tag in re.findall(r"<input\b[^>]*>", source, flags=re.DOTALL)
        if "搜索数据集名称" in tag
    )
    assert 'type="search"' in search_tag
    assert "py-0" in search_tag
    # 两个下拉同为固定高度
    assert source.count("h-[25px]") == 2
    assert source.count("h-[25px] cursor-pointer appearance-none rounded-md border border-gray-200 bg-white py-0") == 2


def test_selected_tags_can_be_removed():
    source = _modal()
    assert "selectedDatasets" in source
    assert '@click="removeSelected(ds.id)"' in source
    assert "已选 {{ selectedDatasets.length }}" in source


def test_select_all_only_applies_to_filtered_rows():
    source = _modal()
    body = source[source.index("const selectAllFiltered"):]
    body = body[: body.index("const clearSelected")]
    assert "filteredDatasets.value.forEach" in body


def test_readonly_hides_bulk_actions():
    """只读态下没有任何绑定操作，批量按钮不该出现。"""
    source = _modal()
    assert 'v-if="!readonly"' in source
    assert ':disabled="readonly"' in source


def test_empty_state_distinguishes_no_match_from_no_data():
    source = _modal()
    assert "没有匹配的数据集" in source
    assert "清除全部筛选" in source
    assert "暂无可绑定元数据集" in source


def test_modal_widens_to_fit_the_filter_row():
    assert 'size="max-w-2xl"' in _modal()


def test_the_sfc_compiles():
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/components/agent/MetadataDatasetBindingModal.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(
        ["node", "-e", script], cwd=str(ROOT / "frontend"), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"弹窗模板无法编译：\n{proc.stderr}"
