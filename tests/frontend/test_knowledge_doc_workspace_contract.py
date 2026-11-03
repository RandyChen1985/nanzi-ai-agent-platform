"""知识库文档工作区的接线契约。

背景：文档从左侧 384px 树里搬进主区工作区，树只负责「选库」。
这次改动的风险集中在三处，全部用契约钉住：

1. 树里不能再渲染文档（否则同一份文档两处可见）；
2. 上传统一等配置项不能跟着文档一起丢，拆成「库设置」页签；
3. 工作区的勾选必须同步 `bulkDocDatasetId`——批量解析/删除读的是它，
   不同步就是「点了按钮什么都没发生」。
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
WORKSPACE = ROOT / "frontend/src/components/knowledge/DocWorkspace.vue"
PROBE = ROOT / "tests/frontend/knowledge_folder_tree.cjs"

pytestmark = pytest.mark.no_infrastructure


def _page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _workspace() -> str:
    return WORKSPACE.read_text(encoding="utf-8")


def test_folder_tree_probe_passes():
    """层级解析必须真跑：老数据单层、新数据多级、同名不同层、环检测。"""
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_document_nodes_are_gone_from_the_left_tree():
    """树里只留库节点；文档列表一旦回到树里，工作区就白做了。"""
    page = _page()
    assert "Document Nodes (Children)" not in page
    assert 'v-show="expandedDatasetIds[dataset.ragflow_dataset_id || dataset.id]"' not in page


def test_lib_info_lives_in_the_drawer():
    """库设置/日志/授权收进抽屉：主区只放文档，配置项一个都不能丢。"""
    page = _page()
    assert "openLibDrawer('settings')" in page
    assert "{ key: 'settings', label: '库设置' }" in page
    assert "v-if=\"detailTab === 'settings'\"" in page
    assert "文档配额" in page


def test_drawer_is_an_overlay_not_a_third_column():
    """抽屉必须浮在主区之上：一旦回到 flex 第三列，列表又被挤窄。

    定位从 ``absolute`` 改成了 ``fixed``（贴视口右上、从顶栏下沿铺到屏幕底部），
    「脱离文档流、不占主区宽度」这个约束不变，所以断言跟着换成 fixed。
    """
    page = _page()
    assert "fixed bottom-0 right-0 top-12" in page
    assert "lg:w-[402px]" in page
    assert "lg:w-[380px] flex-shrink-0" not in page


def test_page_wires_workspace_with_permissions_and_folder_structure():
    """工作区必须拿到真实数据与权限，而不是只读的空壳。"""
    page = _page()
    assert "import DocWorkspace from" in page
    assert ":folder-structure=\"workspaceFolderStructure\"" in page
    assert ":can-manage=\"workspaceCanManage\"" in page
    assert ":root-key=\"workspaceDatasetId\"" in page
    # 写操作一律复用既有函数，不允许组件自己发请求
    assert "void moveDocumentToFolder(dataset, docId, folderPath || null)" in page
    assert "await saveFolderStructure(dataset, structure)" in page


def test_selection_syncs_bulk_dataset_id():
    """批量接口读 bulkDocDatasetId；不同步 = 批量操作静默失效。"""
    page = _page()
    assert "const workspaceUpdateSelection = (ids: string[]) => {" in page
    assert "bulkDocDatasetId.value = ids.length ? workspaceDatasetId.value : ''" in page
    assert '@update:selected-ids="workspaceUpdateSelection"' in page
    # 换库清空上一批勾选，避免把 A 库的 docId 发到 B 库
    assert "selectedDocIds.value = []\n  bulkDocDatasetId.value = dsId" in page


def test_deleting_folder_also_removes_child_paths():
    """多级目录下只删一层 key 会把父节点又「补」回来，看起来像没删掉。"""
    page = _page()
    assert "const prefix = `${folderName}/`" in page
    assert "key === folderName || key.startsWith(prefix)" in page


def test_workspace_owns_no_network_calls():
    """工作区是纯展示层：出现 axios / 接口路径就说明逻辑又分叉了。"""
    src = _workspace()
    assert "axios" not in src
    assert "/api/portal" not in src
    assert "from '@/utils/knowledgeFolders'" in src
    assert "from '@/utils/knowledgeDocStatus'" in src


# ---------------------------------------------------------------------------
# 真渲染：文件夹层级与默认展开
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rendered() -> dict:
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    import json

    proc = subprocess.run(
        ["node", "../tests/frontend/knowledge_doc_workspace_ssr.cjs"],
        cwd=str(ROOT / "frontend"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def test_nested_folders_render_with_top_level_expanded(rendered):
    """顶层文件夹默认展开、子文件夹默认收起——否则一进页面就是几十行文档。"""
    nested = rendered["nested"]
    assert nested["hasTopFolder"] is True
    assert nested["hasChildFolder"] is True
    assert nested["hasSiblingFolder"] is True
    assert nested["showsDocInExpandedTop"] is True
    assert nested["hidesDocInCollapsedChild"] is True
    assert nested["showsUnclassified"] is True


def test_status_reflects_props(rendered):
    """解析失败要能一眼看到。"""
    assert rendered["nested"]["hasParseFailed"] is True
    assert rendered["readOnly"] is True


def test_upload_cta_lives_in_the_library_header():
    """上传入口收在库头：工具条那一排再加一个主按钮就会挤到折行。"""
    page = _page()
    assert "上传文档" in page
    workspace = (ROOT / "frontend/src/components/knowledge/DocWorkspace.vue").read_text()
    assert "上传文档" not in workspace


def test_flat_structure_still_renders(rendered):
    """老数据（单层结构）与「文档全未分类」都必须照常显示。"""
    assert rendered["flat"]["showsUnclassifiedDoc"] is True
    assert rendered["flat"]["noFolderRow"] is True


def test_empty_and_loading_states(rendered):
    assert rendered["empty"] is True
    assert rendered["loading"] is True

def test_quick_views_are_overview_all_favorite_recent():
    """快捷视图固定四项：概览 + 全部/收藏/最近。收藏已接真实数据，最近仍占位。"""
    page = _page()
    for label in ('知识库概览', '全部知识库', '我的收藏', '最近访问'):
        assert label in page
    # 「未分类文档」这一版去掉了（跨库统计没有数据源）
    assert '未分类文档' not in page
    assert 'openLibraryList' in page
    # 收藏：列表接口回 is_favorited，前端据此计数与过滤
    assert 'favoriteCount' in page and 'recentCount' in page
    assert 'is_favorited' in page and 'toggleFavorite' in page
    # 最近访问：Redis 留 30 天，前端只拿顺序
    assert 'recentIds' in page and 'loadRecentVisits' in page
    assert 'recordDatasetVisit' in page


def test_scope_filter_is_a_lightweight_text_row():
    """可见范围是「按范围」下的一行文字筛选，不再单独占一块胶囊 tab。"""
    page = _page()
    assert '按范围' in page
    # 分组标题文案已改名；注释里提到「按部门」不算（那是树分组逻辑的说明）
    assert '>按部门<' not in page
    # 文字 + 下划线，而不是灰底胶囊
    assert 'border-b-2 border-transparent' in page
    assert '!border-primary text-primary' in page


def test_library_list_view_opens_and_rows_enter_dataset():
    """点全部/收藏/最近都落到库列表；列表行点进去等价于左栏点库。"""
    page = _page()
    assert 'v-else-if="listScope"' in page
    assert '@click="selectDatasetNode(ds)"' in page
    assert 'libraryListRows' in page
    assert '图书馆' not in page


def test_bulk_management_moved_to_library_list_page():
    """批量入口从左栏挪到「全部知识库」页：左栏只负责导航，勾选和过滤都跟列表在一起。"""
    page = _page()
    # 左栏不再有独立的「批量」开关
    assert '>批量</button>' not in page
    assert 'bulkSelectMode' not in page
    # 列表页有自己的搜索与范围过滤
    assert 'libraryQuery' in page and 'libraryScope' in page
    # 行前复选框 + 表头全选 + 批量删除
    assert 'toggleLibrarySelectAll' in page
    assert 'toggleDatasetSelection(ds)' in page
    assert '批量删除（{{ librarySelectedCount }}）' in page
    # 没有管理权限的库不允许勾选
    assert ':disabled="!canManageDataset(ds)"' in page


def test_nav_and_body_share_one_card():
    """左栏与主区分成两张卡会在中间留一道缝，看起来像两个区域。"""
    page = _page()
    assert 'lg:flex-row min-h-[calc(100vh-16rem)] items-stretch rounded-2xl border border-gray-200 bg-white' in page
    assert 'lg:border-b-0 lg:border-r' in page
    assert 'placeholder="搜索知识库"' in page


def test_quick_views_can_collapse():
    """快捷视图标题可点：只用部门树时把四行入口让出来，默认仍是展开。"""
    page = _page()
    assert "const quickViewsCollapsed = ref(false)" in page
    assert '@click="quickViewsCollapsed = !quickViewsCollapsed"' in page
    assert '<template v-if="!quickViewsCollapsed">' in page


def test_left_nav_can_collapse():
    """表格列多时把导航整列收起来：收起后主区吃满宽度，左上角留展开按钮。"""
    page = _page()
    assert "navCollapsed" in page
    assert 'title="收起导航"' in page
    assert 'title="展开导航"' in page
    assert 'v-show="!navCollapsed"' in page
    # 折叠后主区左侧要让出悬浮按钮的位置，否则压住库名
    assert "'p-6 pl-14'" in page
