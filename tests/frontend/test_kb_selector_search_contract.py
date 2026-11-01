"""知识库选择器的搜索与筛选契约。

过滤规则被抽到 utils/kbDatasetFilter.ts，因此这里大部分断言是**真跑**：
esbuild 剥掉类型后在 Node 里执行，比对实际过滤结果。组件层只断言接线与结构。
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELECTOR = ROOT / "frontend/src/components/RagFlowResourceSelector.vue"
FILTER_UTIL = ROOT / "frontend/src/utils/kbDatasetFilter.ts"
PROBE = ROOT / "tests/frontend/kb_dataset_filter_probe.cjs"


def _selector() -> str:
    return SELECTOR.read_text(encoding="utf-8")


def _run_probe() -> dict:
    node_modules = ROOT / "frontend/node_modules"
    if not node_modules.exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(
        ["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：过滤规则
# --------------------------------------------------------------------------


def test_keyword_matches_name_description_and_owner():
    """关键词要一次覆盖名称、描述、创建人——用户记得的往往只有其中一个。"""
    out = _run_probe()
    assert out["keyword_by_name"] == ["kb2"]
    assert out["keyword_by_desc"] == ["kb1"]
    assert out["keyword_by_owner"] == ["kb5", "kb2", "kb3"]
    assert out["keyword_case_insensitive"] is True


def test_keyword_also_matches_platform_alias():
    """platform_name 是平台别名，用户看到的是它，必须一起搜。"""
    assert _run_probe()["keyword_by_platform_name"] == ["kb6"]


def test_scope_filter():
    out = _run_probe()
    assert out["scope_team_only"] == ["kb2"]
    assert out["scope_multi"] == ["kb2", "kb3", "kb5"]


def test_updated_within_boundaries():
    """7 天 / 30 天 / 30 天以上必须真的分得开。"""
    out = _run_probe()
    assert out["boundary_7"] == ["d3"]
    assert out["boundary_30"] == ["d3", "d15"]
    assert out["boundary_older"] == ["d60"]


def test_dataset_without_update_time_is_excluded_by_time_filter():
    """时间戳缺失的库不能被「近 7 天」侥幸放进来。"""
    assert _run_probe()["missing_time_excluded"] is True


def test_docs_size_filter():
    out = _run_probe()
    assert out["docs_empty"] == ["kb5"]
    assert out["docs_small"] == ["kb3", "kb4"]
    assert out["docs_large"] == ["kb1", "kb2", "kb6"]


def test_write_access_filter_reads_document_count_alias():
    """RAGFlow 列表接口用 document_count，权限筛选不能因为字段名不同而漏判。"""
    out = _run_probe()
    assert out["write_only"] == ["kb1", "kb2", "kb3", "kb5"]
    assert out["read_only"] == ["kb4", "kb6"]


def test_sorting():
    out = _run_probe()
    assert out["sort_docs"] == ["kb6", "kb1", "kb2", "kb3", "kb4", "kb5"]
    assert out["sort_updated"][0] == "kb5"  # 最近更新排最前
    assert out["sort_name"][0] == "kb1"


def test_sort_tie_breaks_on_id():
    """时间戳相同也要有稳定顺序，否则刚点的位置会跳。"""
    assert _run_probe()["sort_tie_break"] == ["a", "b"]


def test_scope_counts_ignore_current_scope_selection():
    """勾了「TEAM 级」之后，另外两个的数量不能变成 0——否则没法判断换范围能筛出多少。"""
    assert _run_probe()["counts_ignore_current_scope"] is True


def test_scope_counts_follow_keyword():
    assert _run_probe()["counts_follow_keyword"] is True


def test_timestamp_parsing_accepts_seconds_millis_and_iso():
    out = _run_probe()
    assert out["ts_seconds"] is True
    assert out["ts_millis"] is True
    assert out["ts_iso"] is True
    assert out["ts_garbage"] is True


def test_unknown_visibility_falls_back_to_public():
    assert _run_probe()["unknown_visibility_is_public"] is True


def test_has_any_filter():
    out = _run_probe()
    assert out["has_filter_false"] is True
    assert out["has_filter_keyword"] is True
    assert out["has_filter_scope"] is True


# --------------------------------------------------------------------------
# 组件接线
# --------------------------------------------------------------------------


def test_selector_wires_the_filter_utility():
    source = _selector()
    assert "filterAndSortDatasets(" in source
    assert "countByScope(" in source
    assert "hasAnyFilter(" in source


def test_list_renders_filtered_result_and_reports_counts():
    source = _selector()
    assert 'v-for="ds in filteredDatasets"' in source
    assert "筛选出" in source
    assert "全选当前结果" in source


def test_search_controls_only_render_for_dataset_type():
    """智能体列表短，不该跟着多出一堆筛选控件。"""
    source = _selector()
    dataset_branch = source[source.index("<!-- Dataset List -->") :]
    assert 'v-model="keyword"' in dataset_branch
    assert "更多筛选" in dataset_branch


def test_filter_bar_is_sticky():
    """筛选区必须吸顶：列表很长时，滚动中改条件不用滑回顶部。"""
    assert "sticky top-0 z-10" in _selector()


def test_selected_tags_can_be_removed():
    """已选区固定在上方并可逐个移除，避免选错了在长列表里翻找。"""
    source = _selector()
    assert "selectedDatasets" in source
    assert "@click=\"removeSelected(ds.id)\"" in source
    assert "已选 {{ selectedDatasets.length }}" in source


def test_select_all_only_applies_to_filtered_rows():
    """全选只作用于当前筛选结果，不做全库全选。"""
    source = _selector()
    body = source[source.index("const selectAllFiltered"):]
    body = body[: body.index("const clearSelected")]
    assert "filteredDatasets.value.forEach" in body


def test_empty_state_distinguishes_no_match_from_no_data():
    source = _selector()
    assert "没有匹配的知识库" in source
    assert "清除全部筛选" in source
    assert "暂无知识库数据" in source


def test_private_scope_chip_hidden_when_private_excluded():
    """日志库场景下私有库整体不可选，就不该给「个人私有」这个筛选项。"""
    source = _selector()
    assert 'props.excludePrivate ? ["public", "team"] : ["public", "team", "private"]' in source


def test_the_sfc_compiles():
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/components/RagFlowResourceSelector.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(
        ["node", "-e", script], cwd=str(ROOT / "frontend"), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"选择器模板无法编译：\n{proc.stderr}"


def test_fixed_height_form_controls_clear_global_padding():
    """全局 style.css 给 input/select 加了 padding: 8px 12px。

    固定高度的控件若只覆写左右 padding，上下各 8px 会吃掉内容区——
    h-[25px] 的排序下拉只剩 7px 给文字，视觉上就是文字被上下切掉。
    """
    source = _selector()
    import re

    for tag in re.findall(r"<(?:select|input)\b[^>]*>", source, flags=re.DOTALL):
        if 'class="' not in tag:
            continue
        classes = tag.split('class="', 1)[1].split('"', 1)[0]
        fixed_height = "h-[25px]" in classes or "h-9" in classes
        if not fixed_height:
            continue
        assert "py-0" in classes or re.search(r"\bpy-(?:px|0)\b", classes), (
            f"固定高度的表单控件必须用 py-0 显式退出全局上下 padding：{classes[:80]}"
        )
