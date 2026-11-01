"""「数据&知识库」级联菜单的搜索与筛选契约。

过滤规则抽到 utils/mountableItemFilter.ts，核心断言是**真跑**（esbuild 打包后
在 Node 里执行）。组件层重点锁一件容易出错的事：两个页签的筛选条件必须隔离。
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MENU = ROOT / "frontend/src/components/embed/DataKnowledgeCascadeMenu.vue"
PROBE = ROOT / "tests/frontend/mountable_item_filter_probe.cjs"


def _menu() -> str:
    return MENU.read_text(encoding="utf-8")


def _run_probe() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, f"探针执行失败：\n{proc.stderr}"
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# 真跑：过滤规则
# --------------------------------------------------------------------------


def test_keyword_covers_name_id_description_source_and_tags():
    """用户常按数据源名或标签找，只搜名称会漏。"""
    out = _run_probe()
    assert out["kw_name"] == ["7", "1"]
    assert out["kw_id"] == ["53"]
    assert out["kw_desc"] == ["53"]
    assert out["kw_data_source"] == ["99"]
    assert out["kw_tag"] == ["99"]
    assert out["kw_case_insensitive"] is True


def test_keyword_keeps_the_original_three_fields():
    """原来是名称 + ID + 描述，扩字段不能把这三种既有线索弄丢。"""
    assert _run_probe()["kw_keeps_original_scope"] == ["k1"]


def test_data_source_filter():
    assert _run_probe()["ds_filter"] == ["1", "7"]


def test_visibility_filter_falls_back_to_public():
    out = _run_probe()
    assert out["vis_team"] == ["k2"]
    assert out["vis_public"] == ["k1", "k4"]


def test_filters_are_not_isolated_by_resource_kind():
    """条件一旦传入就作用于所有行——按页签隔离是调用方的责任。

    这一条是给组件侧断言的依据：如果哪天有人把 dataSource 和 visibility
    一起传进 currentFilters，两个页签会各自清空，所以组件必须只传当前页签那个。
    """
    out = _run_probe()
    assert out["data_source_not_isolated_by_kind"] is True
    assert out["visibility_not_isolated_by_kind"] is True


def test_size_sort_works_for_both_kinds():
    """同一个「规模」排序项：数据集看表数，知识库看文档数。"""
    out = _run_probe()
    assert out["sort_size_datasets"] == ["99", "1", "53", "7"]
    assert out["sort_size_knowledge"] == ["k1", "k2", "k3", "k4"]


def test_updated_sort_puts_missing_timestamp_last():
    assert _run_probe()["sort_updated"] == ["7", "1", "53", "99"]


def test_name_sort_keeps_chinese_order():
    """只锁中文相对顺序：拉丁与汉字混排位置由 ICU 决定，跨平台会变。"""
    order = _run_probe()["sort_name"]
    assert order.index("99") < order.index("7") < order.index("1")


def test_sort_tie_breaks_on_id():
    assert _run_probe()["sort_tie"] == ["a", "b"]


def test_options_and_counts():
    out = _run_probe()
    assert out["data_sources"] == ["clickhouse", "mysql_aiagent", "postgresql_demo"]
    assert out["visibility_counts"] == {"public": 2, "team": 1, "private": 1}


def test_has_any_filter():
    out = _run_probe()
    assert out["no_filter"] is True
    assert out["kw_filter"] is True
    assert out["ds_only"] is True
    assert out["vis_only"] is True


def test_keyword_and_data_source_combine():
    assert _run_probe()["combined"] == ["7", "1"]


# --------------------------------------------------------------------------
# 组件接线
# --------------------------------------------------------------------------


def test_menu_wires_the_filter_utility():
    source = _menu()
    assert "filterAndSortMountableItems(" in source
    assert "listMountableDataSources(" in source
    assert "hasAnyMountableFilter(" in source


def test_filters_are_isolated_per_tab():
    """数据集页签只传 dataSource，知识库页签只传 visibility。"""
    source = _menu()
    body = source[source.index("const currentFilters = computed<MountableFilters>"):]
    body = body[: body.index("})")]
    assert "dataSource: metaDatasetTab.value ? dataSourceFilter.value : ''" in body
    assert "visibility: metaDatasetTab.value ? '' : visibilityFilter.value" in body


def test_switching_tab_clears_filters():
    """切页签后条件不清会让人以为列表空了。"""
    source = _menu()
    body = source[source.index("const switchTab = (tab: MountTab) =>"):]
    body = body[: body.index("const retry")]
    assert "dataSourceFilter.value = ''" in body
    assert "visibilityFilter.value = ''" in body


def test_filter_controls_render_for_both_tabs():
    source = _menu()
    assert 'data-testid="data-knowledge-filter"' in source
    assert 'data-testid="data-knowledge-sort"' in source
    assert "全部数据源" in source
    assert "全部范围" in source
    assert "授权可见" in source


def test_fixed_height_selects_clear_global_padding():
    """全局 style.css 给 select 加了 padding: 8px 12px，固定高度控件必须 py-0。"""
    source = _menu()
    for tag in re.findall(r"<select\b[^>]*>", source, flags=re.DOTALL):
        classes = tag.split('class="', 1)[1].split('"', 1)[0] if 'class="' in tag else ""
        if "h-[25px]" in classes:
            assert "py-0" in classes, f"固定高度下拉缺 py-0：{classes[:70]}"


def test_list_grows_with_flex_instead_of_fixed_max_height():
    """加了筛选行后，列表若还是固定 max-h 会顶破浮层被裁掉。"""
    source = _menu()
    assert "flex-1 min-h-[10rem]" in source
    assert "max-h-[min(48vh,22rem)]" not in source


def test_empty_state_reacts_to_filters():
    assert "hasActiveFilters ? '没有匹配项'" in _menu()


def test_the_sfc_compiles():
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    script = (
        "const {parse}=require('@vue/compiler-sfc');const fs=require('fs');"
        "const r=parse(fs.readFileSync('src/components/embed/DataKnowledgeCascadeMenu.vue','utf8'));"
        "if(r.errors.length){console.error(r.errors.map(String).join(String.fromCharCode(10)));process.exit(1)}"
    )
    proc = subprocess.run(
        ["node", "-e", script], cwd=str(ROOT / "frontend"), capture_output=True, text=True
    )
    assert proc.returncode == 0, f"菜单模板无法编译：\n{proc.stderr}"
