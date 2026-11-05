"""契约测试：全局数据集权限总览（管理员只读）。

这个页面存在的意义是「不用逐个点进去就知道授权状况」，所以它必须守住四件事：

1. 入口和接口都只对 ``element:metadata:edit`` 开放；
2. **只读**：页面不许直接改授权（全平台批量改的杀伤力太大），只能跳去各数据集的权限页签；
3. 分页：共享库里有一千多个数据集，一次性渲染会把浏览器拖死；
4. summary 按全量统计，筛选只影响列表。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "frontend/src/views/MetadataPermissionOverview.vue"
DATASETS = ROOT / "frontend/src/views/MetadataDatasets.vue"
ROUTER = ROOT / "frontend/src/router/index.ts"
API = ROOT / "app/api/portal/endpoints/metadata.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_route_registered_under_metadata_menu() -> None:
    router = _read(ROUTER)
    assert "'metadata-permissions'" in router
    assert "MetadataPermissionOverview" in router
    block = router[router.index("MetadataPermissionOverview") :][:260]
    assert "menu:metadata" in block


def test_entry_hidden_from_non_admin() -> None:
    page = _read(DATASETS)
    at = page.index('@click="openPermissionOverview()"')
    button_block = page[max(0, at - 600) : at]
    assert "element:metadata:edit" in button_block, "下拉里的入口要挡在 element:metadata:edit 后面"


def test_view_is_read_only() -> None:
    view = _read(VIEW)
    for verb in ("axios.post", "axios.put", "axios.delete", "axios.patch"):
        assert verb not in view, f"总览是只读视图，不应出现 {verb}"
    # 只读的出口：跳到对应数据集的权限页签
    assert "query: { tab: 'permissions' }" in view
    assert "去配置" in view


def test_view_guards_itself() -> None:
    view = _read(VIEW)
    assert "canView" in view
    assert "element:metadata:edit" in view
    assert "只有管理员能看" in view


def test_view_pages_instead_of_rendering_everything() -> None:
    view = _read(VIEW)
    assert "page_size" in view
    assert "totalPages" in view
    assert "上一页" in view and "下一页" in view


def test_backend_endpoint_requires_metadata_edit() -> None:
    api = _read(API)
    start = api.index('"/dataset-permission-overview"')
    block = api[start : start + 400]
    assert "element:metadata:edit" in block


def test_backend_summary_is_independent_of_filter() -> None:
    service = _read(ROOT / "app/services/dataset_permission_overview_service.py")
    # summary 必须在过滤之前算完：注释与代码顺序都锁住，避免以后挪错位置
    summary_at = service.index('"summary": summary')
    filter_at = service.index("filtered = []")
    assert filter_at < summary_at, "summary 要按全量算，不能跟着筛选变"
