"""契约测试：数据集卡片的「更多」菜单。

两个必须守住的点：

1. 卡片容器不能有 ``overflow-hidden``。菜单是向上弹出的绝对定位浮层，
   祖先一旦裁剪就会把菜单顶部切掉（实测「进入数据集」被切掉半行字）。
   底部 footer 的圆角改由自己 ``rounded-b-xl`` 负责。
2. 菜单里的「权限管理」要直接落到详情页的权限页签，而不是再弹一次旧的授权弹窗
   ——否则三层权限（表级/行级）在弹窗里根本看不全。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "frontend/src/views/MetadataDatasets.vue"
TABLES = ROOT / "frontend/src/views/MetadataTables.vue"


def _datasets() -> str:
    return DATASETS.read_text(encoding="utf-8")


def _tables() -> str:
    return TABLES.read_text(encoding="utf-8")


def test_grid_card_does_not_clip_floating_menu() -> None:
    page = _datasets()
    card_line = next(
        line
        for line in page.splitlines()
        if "cursor-pointer group" in line and "flex flex-col relative" in line
    )
    assert "overflow-hidden" not in card_line, "卡片裁剪会把向上弹出的菜单切掉"
    # 去掉裁剪后 footer 要自己收圆角
    assert "bg-gray-50/60 rounded-b-xl" in page


def test_menu_has_permission_management_entry_jumping_to_tab() -> None:
    page = _datasets()
    assert "权限管理" in page
    assert "openPermissionTab" in page
    assert "tab: 'permissions'" in page


def test_menu_keeps_data_permission_config_entry() -> None:
    """「数据权限配置」（成员授权弹窗）不能被「权限管理」挤掉。

    两个入口分工不同：弹窗是快速给角色/用户授权，权限管理页是三层的完整视角。
    用户路径是「元数据管理 → 数据权限配置」，删掉它等于把老入口藏了。
    """
    page = _datasets()
    assert "数据权限配置" in page
    assert '"元数据管理 → 数据权限配置"' in page
    assert "openPermConfigModal(ds)" in page


def test_menu_has_data_row_policy_entry_opening_the_config_modal() -> None:
    """「数据行策略」打开的是配置弹窗，不是跳详情页。

    行级策略的编辑器只有弹窗里有（用户例外 / 角色策略 / 默认策略 + JSON），
    详情页那个「数据行策略」页签只是只读视图，从那里再点过去是绕路。
    """
    page = _datasets()
    assert "数据行策略" in page
    assert '"元数据管理 → 数据行策略"' in page
    assert "openPermConfigModal(ds, true)" in page
    assert "openPermConfigModal(d, true)" in page
    # 弹窗打开后要自动滚到策略区，否则用户还得自己找
    assert "rowPolicySectionRef" in page
    assert "scrollIntoView" in page


def test_search_box_does_not_reflow_while_typing() -> None:
    """输入时搜索框宽度和右侧留白都不能变。

    原来是 type="search"，浏览器自带的清空 ✕ 一出现就吃掉右侧文字区，
    搜索框看着像被"重新排版"了。改成 text + 自绘按钮，右侧恒定留 pr-8。
    """
    page = _datasets()
    box_start = page.index("relative w-full shrink-0 sm:w-72")
    box = page[box_start : box_start + 1400]
    assert 'type="text"' in box
    assert "pr-8" in box
    assert "shrink-0" in box
    assert "清空搜索" in box


def test_list_view_actions_also_collapsed_into_menu() -> None:
    """列表视图原来横着一排 5 个图标，和卡片不一致，也收进「…」。

    「…」按钮本身在表格里，但菜单必须 Teleport 出去：表格容器是 overflow-x-auto，
    行内绝对定位会被裁掉（卡片那轮已经踩过一次）。
    """
    page = _datasets()
    at = page.index('@click.stop="toggleRowMenu(ds, $event)"')
    row_actions = page[max(0, at - 300) : at + 500]
    assert "更多操作" in row_actions
    # 旧的 5 个横排图标入口不应该再出现
    assert 'title="数据权限"' not in page
    assert 'title="删除"' not in page
    # 菜单浮层走 Teleport + fixed 定位
    assert "rowMenuPos" in page
    assert '@wheel.passive="closeRowMenu"' in page


def test_both_menus_carry_the_same_entries() -> None:
    """卡片菜单和列表菜单的条目必须一致，否则同一操作两个入口两种能力。"""
    page = _datasets()
    labels = ["进入数据集", "Schema 巡检", "同步到 RAGFlow", "数据权限配置", "权限管理", "数据行策略", "编辑数据集", "删除数据集"]
    for label in labels:
        assert page.count(label) >= 2, f"「{label}」应该同时出现在卡片菜单和列表菜单里"


def test_detail_view_reads_tab_from_query() -> None:
    page = _tables()
    assert "route.query.tab" in page
    # 只接受白名单，避免 ?tab=乱写 把页面渲染成空白
    assert "VALID_TABS" in page
    assert "'permissions'" in page


def test_list_updated_at_shows_seconds() -> None:
    """更新时间要精确到秒。

    排查「谁在什么时候改的」时，日期只到天根本不够用——尤其是同一天里
    有人改过好几轮权限，看起来两行完全一样。
    """
    page = _datasets()
    assert "formatUpdatedAt" in page
    assert "getSeconds()" in page
    # 不能再退回只显示日期
    assert ".toLocaleDateString()" not in page


def test_list_has_no_extra_view_affordance() -> None:
    """「查看 >」去掉：能做的操作都在「…」里，多一个写着「查看」的箭头会让人以为还有别的入口。"""
    page = _datasets()
    at = page.index('@click.stop="toggleRowMenu(ds, $event)"')
    row_actions = page[max(0, at - 300) : at + 1200]
    assert "查看 <svg" not in row_actions
    # 「…」是操作列最后一个元素
    assert row_actions.index("toggleRowMenu") < row_actions.index("</div>")
