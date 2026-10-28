"""工作台第二个 tab：资源广场，以及两条只读页路由。"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "frontend/src/views/PersonalWorkbench.vue"
ROUTER = ROOT / "frontend/src/router/index.ts"
KB_VIEW = ROOT / "frontend/src/views/ResourceSquareKnowledge.vue"
DS_VIEW = ROOT / "frontend/src/views/ResourceSquareDataset.vue"
PANEL = ROOT / "frontend/src/components/resource-square/ResourceSquarePanel.vue"


def _read(path: Path) -> str:
    assert path.exists(), f"文件不存在：{path}"
    return path.read_text(encoding="utf-8")


def test_workbench_has_square_tab_bound_to_query():
    page = _read(WORKBENCH)
    assert "ResourceSquarePanel" in page
    assert "activeTab" in page
    assert "资源广场" in page
    # tab 与 URL 的解析收敛到 tabFromQuery（工作台/资源广场/技能广场共用一份口径）
    assert "tabFromQuery(route.query.tab)" in page
    assert '"square", "gallery"' in page or "'square', 'gallery'" in page


def test_workbench_keeps_original_body_under_workbench_tab():
    page = _read(WORKBENCH)
    # 原有工作台主体不能被删掉，只是被 tab 包起来
    assert 'v-if="activeTab === \'workbench\'"' in page
    assert "WorkbenchPersonalResources" in page
    assert "WorkbenchAttention" in page


def test_tab_follows_url_for_reused_component():
    """从资源卡跳 ?tab=square 时还在同一路由上，组件复用、setup 不重跑。

    只认初始值的话 URL 变了界面不动——点了卡片像没反应。
    """
    page = _read(WORKBENCH)
    assert "watch(" in page
    assert "route.query.tab" in page


def test_router_registers_two_readonly_routes_without_menu_perm():
    router = _read(ROUTER)
    assert "ResourceSquareKnowledge" in router
    assert "ResourceSquareDataset" in router
    assert "resource-square/knowledge/:datasetId" in router
    assert "resource-square/datasets/:id" in router


def test_readonly_views_offer_apply_path_on_403():
    for path in (KB_VIEW, DS_VIEW):
        view = _read(path)
        assert "去资源广场申请" in view
        assert "resourceSquareErrorStatus" in view


def test_readonly_views_stay_within_readonly_boundary():
    ds_view = _read(DS_VIEW)
    assert "字段明细" in ds_view
    # 不展示字段列表、样例值、YAML
    assert "examples" not in ds_view
    # 注释里会提到 YAML（说明边界），但代码里不能真的去取
    assert "/yaml" not in ds_view
    assert "view_yaml" not in ds_view

    kb_view = _read(KB_VIEW)
    assert "不提供原文下载" in kb_view


def test_refresh_button_actually_refreshes_the_square_tab():
    """站在资源广场点「刷新」必须真的重新拉数据。

    工作台数据由 useWorkbenchHome 管，广场的知识库/数据集两段是面板自己
    onMounted 拉的。只调 refresh()，按钮会转一圈而列表纹丝不动——比没有刷新更糟。
    """
    page = _read(WORKBENCH)
    panel = _read(PANEL)

    # 面板把重新加载暴露出来
    assert "defineExpose({ reloadAll })" in panel

    # 刷新入口按当前 tab 分发
    assert "async function refreshCurrent()" in page
    assert "squarePanel.value?.reloadAll()" in page
    assert 'activeTab.value !== \'square\'' in page


def test_refresh_button_reports_square_busy_state_and_label():
    """广场刷新期间按钮要转、要禁用、文案要说在刷什么。"""
    page = _read(WORKBENCH)
    assert "squareRefreshing" in page
    # busy 汇总三个 tab 的刷新态（工作台 / 资源广场 / 技能广场），按钮禁用直接绑 busy
    assert "const busy = computed(" in page
    assert "squareRefreshing.value" in page
    assert "galleryRefreshing.value" in page
    assert ':disabled="busy"' in page
    assert "刷新资源广场" in page
