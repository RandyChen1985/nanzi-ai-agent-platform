"""知识库分页契约：库列表 / 左侧树 / 库内文档 / 聊天抽屉。

背景：库可能有上千个，单个库又可能有几千份文档。改造前这三个位置都是
「一次全量 + 本地过滤」——文档超过 1000 份还会被静默截断，列表和文档数一起少算。
这次把「分页 + 过滤 + 排序」整体挪到服务端，前端只持有当前页。

契约钉住四件事：

1. 列表页的过滤/排序/分页参数必须**一起**下发：本地再过滤一次会漏掉其他页的命中，
   页码与总数也会对不上；
2. 左树按部门增量渲染，不做页码（页码 + 层级 + 折叠态叠加，用户记不住自己在第几页）；
3. 库内文档的关键字/状态/排序/文件夹全部走接口，组件不许再本地过滤；
4. 分页条只有一份实现，页码算法只有一处。
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_infrastructure

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "frontend/src/views/KnowledgeBaseManagement.vue"
WORKSPACE = ROOT / "frontend/src/components/knowledge/DocWorkspace.vue"
BAR = ROOT / "frontend/src/components/common/PaginationBar.vue"
WINDOW = ROOT / "frontend/src/utils/paginationWindow.ts"
DRAWER = ROOT / "frontend/src/components/knowledge/KnowledgePortalDrawer.vue"
PORTAL = ROOT / "frontend/src/composables/useKnowledgePortal.ts"
PROBE = ROOT / "tests/frontend/pagination_window_probe.cjs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_window_probe_passes():
    """页码窗口必须真跑：边界（≤7 / 贴着首尾 / 总数为 0）只有算一遍才看得见。"""
    if not (ROOT / "frontend/node_modules").exists():
        pytest.skip("前端依赖未安装")
    proc = subprocess.run(["node", str(PROBE)], cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# 一、库列表：服务端分页
# ---------------------------------------------------------------------------


def test_library_list_filters_and_paging_go_to_the_server():
    """搜索 / 可见范围 / 失联 / 收藏 / 最近 / 排序 / 页码，一个都不能留在本地。"""
    page = _read(PAGE)
    block = page.split("const fetchLibraryList = async", 1)[1].split("const onLibraryQueryInput", 1)[0]

    assert "page: libraryPage.value" in block
    assert "page_size: libraryPageSize.value" in block
    assert "sort: librarySort.value" in block
    assert "params.keyword = keyword" in block
    assert "params.visibility = libraryScope.value" in block
    assert "params.missing = 1" in block
    assert "params.favorite = 1" in block
    assert "params.ids = recentIds.value.join(',')" in block

    # total 取后端返回值，不能再用「当前页条数」冒充
    assert "response.data?.total" in block
    assert "libraryRows.value = apiData(response)" in block


def test_library_list_defaults_to_twenty_per_page():
    """默认每页 20：库多的部门里，50/100 一屏根本扫不完。"""
    page = _read(PAGE)
    assert "const libraryPageSize = ref(20)" in page
    assert "const LIBRARY_PAGE_SIZES = [20, 50, 100]" in page


def test_library_list_page_resets_on_filter_change():
    """改搜索词/范围/排序/每页条数都要回第 1 页，否则会停在一个不存在的页上。"""
    page = _read(PAGE)
    for handler in (
        "const onLibraryQueryInput",
        "const onLibraryScopeChange",
        "const onLibrarySortChange",
        "const onLibraryPageSizeChange",
    ):
        block = page.split(handler, 1)[1].split("\n}", 1)[0]
        assert "libraryPage.value = 1" in block, f"{handler} 没有把页码复位"


def test_library_pager_is_mounted_and_shares_one_implementation():
    """分页条常驻页面底部，且只有一份实现（页面里不许再手写上一页/下一页）。"""
    page = _read(PAGE)
    assert "from '../components/common/PaginationBar.vue'" in page
    assert "<PaginationBar" in page
    assert ':total="libraryTotal"' in page
    assert '@update:page="onLibraryPageChange"' in page
    assert '@update:page-size="onLibraryPageSizeChange"' in page
    # 页面自己不再拼页码窗口
    assert "buildPageWindow" not in page


# ---------------------------------------------------------------------------
# 二、左侧树：按部门增量渲染
# ---------------------------------------------------------------------------


def test_tree_renders_incrementally_per_department():
    """一个部门下几百个库时不能全渲染；但不做页码，用「再显示 N 个」扩张。"""
    page = _read(PAGE)
    assert "const TREE_GROUP_PAGE = 6" in page
    assert "const TREE_GROUP_STEP = 10" in page
    assert 'v-for="dataset in visibleGroupDatasets(group)"' in page
    assert "expandTreeGroup(group)" in page
    assert "再显示" in page


def test_tree_does_not_truncate_search_results():
    """搜索命中已经是用户主动缩小的集合，再截断会像「搜不到」。"""
    page = _read(PAGE)
    block = page.split("const visibleGroupDatasets = (group: DeptDatasetGroup)", 1)[1].split(
        "const treeGroupRemaining", 1
    )[0]
    assert "searchQuery.value.trim()" in block
    assert "return group.datasets" in block


# ---------------------------------------------------------------------------
# 三、库内文档：服务端分页 + 文件夹是过滤维度
# ---------------------------------------------------------------------------


def test_document_list_passes_filters_and_paging_to_the_api():
    page = _read(PAGE)
    block = page.split("const fetchDocumentsForDataset = async", 1)[1].split(
        "// 更新对应知识库的文档数量计数", 1
    )[0]

    assert "page: docPage.value" in block
    assert "page_size: docPageSize.value" in block
    assert "sort: docSort.value" in block
    assert "params.name = keyword" in block
    assert "params.status = docStatus.value" in block
    assert "params.folder = docFolder.value" in block


def test_document_endpoint_response_fields_are_consumed():
    """后端回了 total/all_total/status_counts/folders，前端必须用上。"""
    page = _read(PAGE)
    for field in ("response.data?.total", "response.data?.all_total", "response.data?.status_counts", "response.data?.folders"):
        assert field in page, f"{field} 没被读取：分页总数与文件夹计数会退回「当前页条数」"


def test_document_count_uses_all_total_not_page_length():
    """库头文档数用全库总数：用当前页条数会显示「这个库只有 50 份」。"""
    page = _read(PAGE)
    block = page.split("// 更新对应知识库的文档数量计数", 1)[1].split("checkAndStartDatasetPolling", 1)[0]
    assert "dataset.doc_count = docAllTotal.value" in block
    assert "dataset.document_count = docAllTotal.value" in block
    assert "docs.length" not in block


def test_workspace_is_a_controlled_component():
    """筛选/排序/页码归页面所有：组件留一份本地 state 就会出现两个真相。"""
    workspace = _read(WORKSPACE)
    for prop in ("total?: number", "page?: number", "pageSize?: number", "keyword?: string", "status?: string", "sort?: string", "folder?: string"):
        assert prop in workspace, f"DocWorkspace 缺少受控 prop {prop}"
    for event in ("update:page", "update:pageSize", "update:keyword", "update:status", "update:sort", "update:folder"):
        assert f"(event: '{event}'" in workspace, f"DocWorkspace 缺少 {event} 事件"
    # 本地过滤变量必须清干净，否则「服务端过滤 + 本地再过滤」双重生效
    assert "const statusFilter = ref" not in workspace
    assert "const currentPath = ref" not in workspace
    assert "const query = ref" not in workspace


def test_workspace_no_longer_expands_folders_like_a_tree():
    """文件夹改成 chip + 面包屑：树形展开和页码会互相打架。"""
    workspace = _read(WORKSPACE)
    assert "const expanded = ref" not in workspace
    assert "data-folder-path" in workspace
    assert "openFolder(node.path)" in workspace
    assert "UNCLASSIFIED" in workspace


def test_workspace_page_change_reaches_the_api():
    """组件 emit 出来的页码要能真的变成一次请求。"""
    page = _read(PAGE)
    for handler in ("onDocPageChange", "onDocPageSizeChange", "onDocKeywordInput", "onDocStatusChange", "onDocSortChange", "onDocFolderChange"):
        assert f"const {handler} = " in page, f"缺少 {handler}"
    assert '@update:page="onDocPageChange"' in page
    assert '@update:folder="onDocFolderChange"' in page
    # 任何一次筛选变化都会重取当前页
    block = page.split("const onDocPageChange = ", 1)[1].split("const workspaceUpdateSelection", 1)[0]
    assert "refreshWorkspaceDocs()" in block


def test_folder_filter_changes_also_refresh_the_page():
    """新建/重命名/删除/移动文件夹后，当前视图可能已不包含那份文档。"""
    page = _read(PAGE)
    block = page.split("const saveFolderStructure = async", 1)[1].split("/** 文档被删除后清理目录里的悬空引用", 1)[0]
    assert "refreshWorkspaceDocs()" in block


# ---------------------------------------------------------------------------
# 四、分页条与聊天抽屉
# ---------------------------------------------------------------------------


def test_pagination_bar_delegates_page_math():
    """页码窗口只算一次：组件里再写一遍 Math.ceil 就等着两处漂移。"""
    bar = _read(BAR)
    assert "from '@/utils/pagination'" in bar
    assert "from '@/utils/paginationWindow'" in bar
    assert "buildPageWindow" in bar
    assert "Math.ceil" not in bar
    assert "emit('update:page', next)" in bar
    assert "emit('update:pageSize', value)" in bar


def test_page_window_helpers_live_in_one_place():
    window = _read(WINDOW)
    for name in ("totalPagesOf", "clampPage", "buildPageWindow", "pageRangeOf"):
        assert f"export function {name}" in window


def test_portal_drawer_can_load_more_documents():
    """抽屉原来固定取前 100 份且没有加载更多：第 101 份永远看不到。"""
    portal = _read(PORTAL)
    assert "const DOC_PAGE_SIZE = 100" in portal
    assert "const loadMoreDatasetDocuments = async" in portal
    assert "loadMoreDatasetDocuments," in portal
    # 抽屉里要有入口，父组件要能接住
    drawer = _read(DRAWER)
    assert 'e: "load-more-documents"' in drawer
    assert "load-more-documents" in drawer
    assert "加载更多" in drawer
    for view in ("frontend/src/views/AgentDebug.vue", "frontend/src/views/EmbedChat.vue"):
        src = _read(ROOT / view)
        assert '@load-more-documents="loadMoreDatasetDocuments"' in src, f"{view} 没有接住加载更多"
        assert "loadMoreDatasetDocuments," in src
